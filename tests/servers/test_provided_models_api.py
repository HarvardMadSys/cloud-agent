"""Provided models, as an owner meets them: offered with no setup, run within a daily budget."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from tests.servers.test_agent_jobs_api import (  # noqa: F401 -- autouse fixtures
    _OWNER,
    FakeStorage,
    _api_key_secret,
    _build_app,
    _FakeOwners,
)
from tests.support.owners import CONNECTION_ID, connection_row

from cloud_agent.api.deps import get_owner_directory
from cloud_agent.application.operator_models import provided_default, provided_sources
from cloud_agent.domain.operator_models import Allowance
from cloud_agent.schemas.model_preferences import ModelPreferenceView
from cloud_agent.usage.turn_report import usage_source

pytestmark = pytest.mark.asyncio

PROVIDED = "oconn_" + "e" * 32


def _provided_row(**changes: Any) -> dict[str, Any]:
    row = connection_row("nobody", connection_id=PROVIDED, models=("glm-5.1", "kimi-x"))
    row.pop("user_id")
    return {
        **row,
        "name": "Campus models",
        "is_default": True,
        "default_model": "glm-5.1",
        "user_concurrency": 4,
        "end_user_header": None,
        **changes,
    }


class _Store(FakeStorage):
    """The jobs fake, with one provided connection and this owner's budget on it.

    $5 a day, $2 of it spent, and ``kimi-x`` withheld from this owner.
    """

    def __init__(self, *, own_connection: bool = True) -> None:
        super().__init__()
        self.provided: dict[str, Any] | None = _provided_row()
        self.budget, self.spent = 5_000_000, 2_000_000
        self.withheld = {"kimi-x"}
        self.own_connection = own_connection

    def _allowance(self, model: str) -> Allowance:
        return Allowance(
            offered=model not in self.withheld and self.budget > 0,
            budget_microusd=self.budget,
            spent_microusd=self.spent,
        )

    async def list_model_connections(self, *, user_id):
        return [connection_row(user_id)] if self.own_connection else []

    async def operator_sources_for_user(self, *, user_id):
        if self.provided is None or not self.provided["enabled"]:
            return []
        return [
            {
                **self.provided,
                "allowances": {model: self._allowance(model) for model in self.provided["models"]},
                "budget_microusd": self.budget,
                "spent_microusd": self.spent,
            }
        ]

    async def operator_connection(self, *, connection_id):
        return self.provided if connection_id == PROVIDED else None

    async def operator_allowance(self, *, connection_id, user_id, model):
        return self._allowance(model)


async def _config(store: _Store) -> dict[str, Any]:
    app = _build_app(store)
    app.dependency_overrides[get_owner_directory] = _FakeOwners
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        response = await client.get("/v1/agent/config")
    assert response.status_code == 200, response.text
    return response.json()


async def _create(store: _Store, **body: Any):
    app = _build_app(store)
    app.dependency_overrides[get_owner_directory] = _FakeOwners
    job = {
        "repo": None,
        "task_prompt": "fix it",
        "runtime": "opencode",
        "model": "glm-5.1",
        "model_connection_id": PROVIDED,
        **body,
    }
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        return await client.post("/v1/agent/jobs", json=job)


async def test_an_owner_with_no_connection_of_their_own_can_start_at_once() -> None:
    config = await _config(_Store(own_connection=False))
    assert config["model_connections"] == []
    assert config["model_connection_required"] is False
    # Nothing ran before, so a new task starts where the deployment says.
    assert (config["default_model"], config["default_model_connection_id"]) == (
        "glm-5.1",
        PROVIDED,
    )
    [source] = config["provided_sources"]
    assert source["id"] == PROVIDED and source["name"] == "Campus models"
    # A model withheld from this owner is not listed at all.
    assert source["models"] == ["glm-5.1"]
    assert list(source["model_runtimes"]) == ["glm-5.1"]
    # One budget for the source's models together, and what was spent of it today.
    assert (source["daily_budget_microusd"], source["spent_today_microusd"]) == (
        5_000_000,
        2_000_000,
    )
    assert "allowances" not in source
    assert datetime.fromisoformat(source["resets_at"]) > datetime.now(UTC)
    # Nothing of the connection's endpoint or key is described to an owner.
    assert "base_url" not in source and "api_key" not in str(config)


async def test_provided_models_sit_beside_the_owners_own() -> None:
    config = await _config(_Store())
    assert [option["id"] for option in config["model_connections"]] == [CONNECTION_ID]
    assert [source["id"] for source in config["provided_sources"]] == [PROVIDED]
    assert config["model_connection_required"] is False


async def test_a_new_task_starts_where_the_last_one_ran_whichever_kind_that_was() -> None:
    store = _Store()

    async def last_on_own(user_id: str) -> ModelPreferenceView:
        return ModelPreferenceView(last_model="glm-5.1", last_model_connection_id=CONNECTION_ID)

    store.model_preferences.get_model_preference.side_effect = last_on_own
    config = await _config(store)
    assert config["default_model_connection_id"] == CONNECTION_ID

    async def last_on_provided(user_id: str) -> ModelPreferenceView:
        return ModelPreferenceView(last_model="glm-5.1", last_model_connection_id=PROVIDED)

    store.model_preferences.get_model_preference.side_effect = last_on_provided
    assert (await _config(store))["default_model_connection_id"] == PROVIDED

    # The model it ran on was since withheld from this owner: back to the deployment's start.
    async def last_on_withheld(user_id: str) -> ModelPreferenceView:
        return ModelPreferenceView(last_model="kimi-x", last_model_connection_id=PROVIDED)

    store.model_preferences.get_model_preference.side_effect = last_on_withheld
    config = await _config(store)
    assert (config["default_model"], config["default_model_connection_id"]) == (
        "glm-5.1",
        PROVIDED,
    )


async def test_a_default_on_a_spent_budget_is_not_preselected() -> None:
    store = _Store(own_connection=False)
    store.spent = store.budget
    config = await _config(store)
    assert config["default_model"] is None
    # Still listed, with its numbers, so the composer can say when it starts over.
    [source] = config["provided_sources"]
    assert source["spent_today_microusd"] == source["daily_budget_microusd"] == 5_000_000


async def test_nothing_is_offered_while_connections_are_switched_off(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_CONNECTIONS_ENABLED", "0")
    config = await _config(_Store())
    assert config["provided_sources"] == [] and config["model_connections"] == []
    assert config["model_connection_required"] is True


@pytest.mark.parametrize("change", [{"withheld": {"glm-5.1", "kimi-x"}}, {"budget": 0}])
async def test_a_source_with_nothing_for_this_owner_is_not_listed(change) -> None:
    store = _Store(own_connection=False)
    for name, value in change.items():
        setattr(store, name, value)
    config = await _config(store)
    assert config["provided_sources"] == [] and config["model_connection_required"] is True


async def test_a_task_runs_on_a_provided_model_by_naming_its_source() -> None:
    store = _Store(own_connection=False)
    response = await _create(store)
    assert response.status_code == 201, response.text
    [job] = store.jobs.values()
    assert (job["model"], job["model_connection_id"]) == ("glm-5.1", PROVIDED)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"spent": 5_000_000}, "allowance_exhausted"),
        ({"withheld": {"glm-5.1"}}, "allowance_none"),
        ({"budget": 0}, "allowance_none"),
        ({"provided": {"enabled": False}}, "provided_unavailable"),
        ({"provided": {"models": ["kimi-x"]}}, "provided_model_unavailable"),
        ({"provided": None}, "provided_unavailable"),
    ],
)
async def test_a_task_the_source_cannot_run_is_refused_with_the_reason(change, reason) -> None:
    store = _Store()
    if "provided" not in change:
        for name, value in change.items():
            setattr(store, name, value)
    elif change["provided"] is None:
        store.provided = None
    else:
        assert store.provided is not None
        store.provided.update(change["provided"])
    response = await _create(store)
    assert response.status_code == 400, response.text
    error = response.json()["detail"]["error"]
    assert error["type"] == "model_connection_unusable"
    assert error["reason"] == reason
    # Never moved to the owner's own connection instead.
    assert store.jobs == {}


async def test_another_owners_source_id_shape_is_still_validated() -> None:
    response = await _create(_Store(), model_connection_id="oconn_not-an-id")
    assert response.status_code == 422


async def test_sources_are_narrowed_to_what_this_owner_may_use() -> None:
    store = _Store()
    store.withheld = set()
    [source] = await provided_sources(store, user_id=_OWNER)
    assert source["models"] == ["glm-5.1", "kimi-x"]
    assert (source["daily_budget_microusd"], source["spent_today_microusd"]) == (
        5_000_000,
        2_000_000,
    )
    assert provided_default([source]) == ("glm-5.1", PROVIDED)
    # A budget used up to the last millionth starts nothing either.
    assert provided_default([{**source, "spent_today_microusd": 5_000_000}]) is None
    # Not the default connection, or a default model this owner cannot run: no start.
    assert provided_default([{**source, "is_default": False}]) is None
    assert provided_default([{**source, "default_model": None}]) is None


async def test_usage_says_whose_connection_a_turn_ran_on() -> None:
    assert usage_source(PROVIDED) == "provided"
    assert usage_source(CONNECTION_ID) == "personal"
    assert usage_source(None) is None
