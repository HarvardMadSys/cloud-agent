"""Provided models, as an owner meets them: offered with no setup, the same to every owner."""

from __future__ import annotations

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
        **changes,
    }


class _Store(FakeStorage):
    """The jobs fake, with one provided connection listing two models."""

    def __init__(self, *, own_connection: bool = True) -> None:
        super().__init__()
        self.provided: dict[str, Any] | None = _provided_row()
        self.own_connection = own_connection

    async def list_model_connections(self, *, user_id):
        return [connection_row(user_id)] if self.own_connection else []

    async def enabled_operator_connections(self):
        if self.provided is None or not self.provided["enabled"]:
            return []
        return [self.provided]

    async def operator_connection(self, *, connection_id):
        return self.provided if connection_id == PROVIDED else None


def _unusable(store: _Store, model: str) -> None:
    """No agent can use ``model``: its verification found nothing."""
    assert store.provided is not None
    checks = dict(store.provided["model_checks"])
    checks.pop(model)
    store.provided["model_checks"] = checks


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
    # Nothing ran before, so a new task starts on the source's first model.
    assert (config["default_model"], config["default_model_connection_id"]) == (
        "glm-5.1",
        PROVIDED,
    )
    [source] = config["provided_sources"]
    assert source["id"] == PROVIDED and source["name"] == "Campus models"
    # Every model the key lists, in the provider's order.
    assert source["models"] == ["glm-5.1", "kimi-x"]
    assert list(source["model_runtimes"]) == ["glm-5.1", "kimi-x"]
    # No budget, nothing spent: nothing here limits an owner.
    assert not {"daily_budget_microusd", "spent_today_microusd", "resets_at"} & set(source)
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

    # The model it ran on is no longer usable: back to the deployment's start.
    async def last_on_unusable(user_id: str) -> ModelPreferenceView:
        return ModelPreferenceView(last_model="kimi-x", last_model_connection_id=PROVIDED)

    store.model_preferences.get_model_preference.side_effect = last_on_unusable
    _unusable(store, "kimi-x")
    config = await _config(store)
    assert (config["default_model"], config["default_model_connection_id"]) == (
        "glm-5.1",
        PROVIDED,
    )


async def test_a_new_owner_starts_on_the_first_model_an_agent_can_use() -> None:
    store = _Store(own_connection=False)
    _unusable(store, "glm-5.1")
    config = await _config(store)
    assert (config["default_model"], config["default_model_connection_id"]) == (
        "kimi-x",
        PROVIDED,
    )
    # A model no agent can use is offered to nobody.
    [source] = config["provided_sources"]
    assert source["models"] == ["kimi-x"]


async def test_nothing_is_offered_while_connections_are_switched_off(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_MODEL_CONNECTIONS_ENABLED", "0")
    config = await _config(_Store())
    assert config["provided_sources"] == [] and config["model_connections"] == []
    assert config["model_connection_required"] is True


async def test_a_source_with_no_usable_model_is_not_listed() -> None:
    store = _Store(own_connection=False)
    _unusable(store, "glm-5.1")
    _unusable(store, "kimi-x")
    config = await _config(store)
    assert config["provided_sources"] == [] and config["model_connection_required"] is True
    assert config["default_model"] is None


async def test_a_task_runs_on_a_provided_model_by_naming_its_source() -> None:
    store = _Store(own_connection=False)
    response = await _create(store)
    assert response.status_code == 201, response.text
    [job] = store.jobs.values()
    assert (job["model"], job["model_connection_id"]) == ("glm-5.1", PROVIDED)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"provided": {"enabled": False}}, "provided_unavailable"),
        ({"provided": {"models": ["kimi-x"]}}, "provided_model_unavailable"),
        ({"provided": None}, "provided_unavailable"),
    ],
)
async def test_a_task_the_source_cannot_run_is_refused_with_the_reason(change, reason) -> None:
    store = _Store()
    if change["provided"] is None:
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


async def test_every_owner_is_offered_the_same_sources_oldest_first() -> None:
    store = _Store()
    older = store.provided
    assert older is not None
    newer = {**older, "id": "oconn_" + "f" * 32, "name": "Later"}

    async def both():
        return [older, newer]

    store.enabled_operator_connections = both  # type: ignore[method-assign]
    sources = await provided_sources(store)
    assert [source["id"] for source in sources] == [PROVIDED, newer["id"]]
    assert sources[0]["models"] == ["glm-5.1", "kimi-x"]
    # The oldest source's first usable model; a source with none is skipped.
    assert provided_default(sources) == ("glm-5.1", PROVIDED)
    nothing = {**sources[0], "model_runtimes": {}}
    assert provided_default([nothing, sources[1]]) == ("glm-5.1", newer["id"])
    assert provided_default([nothing]) is None


async def test_usage_says_whose_connection_a_turn_ran_on() -> None:
    assert usage_source(PROVIDED) == "provided"
    assert usage_source(CONNECTION_ID) == "personal"
    assert usage_source(None) is None
