"""Admin routes for the models a deployment provides: what the key lists, verified, audited."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cloud_agent.api import admin_operator_models
from cloud_agent.api.deps import get_storage
from cloud_agent.application.model_connection_check import CheckStop, ListedModel
from cloud_agent.application.model_connection_save import (
    VerificationRefused,
    VerificationTimedOut,
)
from cloud_agent.application.model_connection_status import verification_status
from cloud_agent.application.model_connections import operator_connection_cipher
from cloud_agent.domain.model_connections import ModelConnectionConflict
from cloud_agent_shared.model_connections import MAX_MODELS

pytestmark = pytest.mark.asyncio

ADMIN = {"Authorization": "Bearer operator-models-admin"}
PATH = "/admin/agent/operator-model-connections"
_KEY = "sk-deployment-key-0123456789"
_NOW = datetime(2026, 11, 12, 9, tzinfo=UTC)
_SUPPORTED = {"protocols": {"chat_completions": {"status": "supported"}}}
_FAILED = {"protocols": {"chat_completions": {"status": "failed", "category": "auth"}}}
#: As hybridInference lists them: US dollars per million tokens.
_PRICING = {"prompt": "2.8", "completion": "8.8", "input_cache_reads": "0.8", "image": "0"}
#: The same, as stored and answered: micro-dollars per million tokens.
_PRICES = {
    "input_price": 2_800_000,
    "output_price": 8_800_000,
    "cache_read_price": 800_000,
    "cache_write_price": None,
}


@pytest.fixture(autouse=True)
def _keys(monkeypatch):
    monkeypatch.setenv("ADMIN_TOKEN", "operator-models-admin")
    monkeypatch.setenv("AGENT_SETTINGS_ENCRYPTION_KEY", "operator-models-settings-root-key")


class _Repository:
    """Provided connections in memory, with the rules the Postgres one enforces."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.prices: dict[tuple[str, str], Any] = {}
        self.usage_rows: list[dict[str, Any]] = []
        self.released: list[str] = []
        self.relay_hosts = 1
        self.moved_during_verification = False

    async def list_operator_connections(self):
        return [row for row in self.rows.values() if row["deleted_at"] is None]

    async def get_operator_connection(self, *, connection_id):
        return self.rows.get(connection_id)

    async def create_operator_connection(self, *, values):
        if any(
            row["name"].lower() == values["name"].lower()
            for row in await self.list_operator_connections()
        ):
            raise ModelConnectionConflict(values["name"])
        row = {
            **values,
            "model_checks": {m: {**r, "revision": 1} for m, r in values["model_checks"].items()},
            "enabled": False,
            "config_revision": 1,
            "credential_revision": 1,
            "key_refused_at": None,
            "created_at": _NOW,
            "updated_at": _NOW,
            "deleted_at": None,
        }
        self.rows[row["id"]] = row
        return row

    async def update_operator_connection(self, *, connection_id, changes):
        row = self.rows[connection_id]
        assert set(changes) <= {"name", "models", "enabled"}, changes
        if changes.get("enabled") is False and row["enabled"]:
            row["credential_revision"] += 1
        if "models" in changes:
            assert set(changes["models"]) <= set(row["models"]), "an unverified edit only shrinks"
            row["model_checks"] = {
                m: r for m, r in row["model_checks"].items() if m in changes["models"]
            }
            self._forget(connection_id, changes["models"])
        row.update(changes)
        return row

    async def commit_verified_operator_update(
        self, *, connection_id, expected, changes, model_checks
    ):
        assert set(changes) <= {"name", "models", "base_url", "api_key_ciphertext"}, changes
        if self.moved_during_verification:
            return None
        row = self.rows[connection_id]
        endpoint = "base_url" in changes or "api_key_ciphertext" in changes
        if endpoint:
            row["config_revision"] += 1
        if "base_url" in changes:
            row["credential_revision"] += 1
        if "api_key_ciphertext" in changes:
            row["key_refused_at"] = None
        row.update(changes)
        row["model_checks"] = {
            m: {**r, "revision": row["config_revision"]} for m, r in model_checks.items()
        }
        if "models" in changes:
            self._forget(connection_id, changes["models"])
        return row

    def _forget(self, connection_id: str, models: list[str]) -> None:
        for key in [key for key in self.prices if key[0] == connection_id]:
            if key[1] not in models:
                del self.prices[key]

    async def delete_operator_connection(self, *, connection_id):
        row = self.rows[connection_id]
        row.update(deleted_at=_NOW, enabled=False, api_key_ciphertext=None)
        return row

    async def model_relay_host_count(self):
        return self.relay_hosts

    async def operator_prices(self, *, connection_ids):
        found: dict[str, dict[str, Any]] = {cid: {} for cid in connection_ids}
        for (cid, model), prices in self.prices.items():
            if cid in found:
                found[cid][model] = prices
        return found

    async def set_operator_prices(self, *, connection_id, prices):
        listed = self.rows[connection_id]["models"]
        for model, price in prices.items():
            if price is None:
                self.prices.pop((connection_id, model), None)
            elif model in listed:
                self.prices[(connection_id, model)] = price

    async def operator_usage(self, *, connection_id, start, end, limit):
        return [row for row in self.usage_rows if start <= row["day"] <= end][:limit]

    async def release_stale_operator_residencies(self, *, connection_id):
        self.released.append(connection_id)
        return []


class _Accounts:
    emails: ClassVar[dict[str, str]] = {"01HZXACCOUNTAAAAAAAAAAAAAAA": "a@example.edu"}

    async def account_emails(self, account_ids):
        return {aid: self.emails[aid] for aid in account_ids if aid in self.emails}


def _listing(*models: str, pricing: dict[str, Any] | None = _PRICING) -> list[ListedModel]:
    return [ListedModel(model, pricing) for model in models]


class _Harness:
    def __init__(self, monkeypatch) -> None:
        self.repository = _Repository()
        self.audit: list[dict[str, Any]] = []
        self.listed: list[dict[str, Any]] = []
        self.verified: list[dict[str, Any]] = []
        self.results: dict[str, dict[str, Any]] = {}
        self.listing: list[ListedModel] | CheckStop = _listing("model-1", "model-2")
        self.timeout = False
        harness = self

        class Audit:
            async def record_admin_action(self, **row):
                harness.audit.append(row)
                return len(harness.audit)

        self.store = SimpleNamespace(
            operator_models=self.repository,
            audit=Audit(),
            local_accounts=_Accounts(),
            host_pool=SimpleNamespace(),
        )

        async def discover_listing(*, base_url, api_key):
            harness.listed.append({"base_url": base_url, "api_key": api_key})
            if isinstance(harness.listing, CheckStop):
                raise harness.listing
            return list(harness.listing)

        async def verify_endpoint(*, base_url, api_key, models, every=True):
            assert every is False, "a provided connection keeps what some agent can use"
            harness.verified.append({"base_url": base_url, "api_key": api_key, "models": models})
            if harness.timeout:
                raise VerificationTimedOut
            results = {model: harness.results.get(model, _SUPPORTED) for model in models}
            statuses = [verification_status(model, results[model]) for model in models]
            if all(not status["runtimes"] for status in statuses):
                raise VerificationRefused(statuses)
            return results

        monkeypatch.setattr(admin_operator_models, "discover_listing", discover_listing)
        monkeypatch.setattr(admin_operator_models, "verify_endpoint", verify_endpoint)
        self.app = FastAPI()
        self.app.include_router(admin_operator_models.router)
        self.app.dependency_overrides[get_storage] = lambda: self.store

    async def call(self, method: str, path: str = "", *, headers=ADMIN, **kwargs):
        transport = ASGITransport(app=self.app)
        async with AsyncClient(transport=transport, base_url="http://admin") as client:
            return await client.request(method, PATH + path, headers=headers, **kwargs)

    async def create(self, **changes):
        body = {
            "name": "Provider",
            "base_url": "https://provider.example/v1/",
            "api_key": _KEY,
            **changes,
        }
        return await self.call("POST", json=body)

    def actions(self) -> list[str]:
        return [row["action"] for row in self.audit]


@pytest.fixture
def admin(monkeypatch) -> _Harness:
    return _Harness(monkeypatch)


async def _created(admin: _Harness, **changes) -> dict[str, Any]:
    response = await admin.create(**changes)
    assert response.status_code == 201, response.text
    return response.json()


async def _enabled(admin: _Harness) -> dict[str, Any]:
    view = await _created(admin)
    response = await admin.call("PATCH", f"/{view['id']}", json={"enabled": True})
    assert response.status_code == 200, response.text
    admin.audit.clear()
    admin.listed.clear()
    admin.verified.clear()
    admin.repository.released.clear()
    return response.json()


# -- who ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", ""),
        ("POST", ""),
        ("PATCH", "/x"),
        ("DELETE", "/x"),
        ("POST", "/x/refresh-models"),
        ("GET", "/x/usage"),
    ],
)
async def test_every_route_is_an_administrators(admin, method, path) -> None:
    response = await admin.call(method, path, headers={}, json={})
    assert response.status_code in (401, 403)
    assert admin.repository.rows == {} and admin.audit == [] and admin.listed == []


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("PUT", "/x/terms"),
        ("GET", "/x/users"),
        ("PUT", "/x/user-budgets"),
        ("DELETE", "/x/user-budgets"),
        ("PUT", "/x/user-access"),
        ("DELETE", "/x/user-access"),
    ],
)
async def test_nothing_is_set_per_model_or_per_account_any_more(admin, method, path) -> None:
    response = await admin.call(method, path, json={})
    assert response.status_code in (404, 405)


# -- saving ------------------------------------------------------------------------


async def test_a_connection_lists_what_its_key_lists_verified_disabled_and_keyless(
    admin,
) -> None:
    response = await admin.create()
    assert response.status_code == 201
    assert response.headers["cache-control"] == "no-store"
    view = response.json()
    assert view["id"].startswith("oconn_") and view["enabled"] is False
    assert view["base_url"] == "https://provider.example/v1"
    # The provider's listing for this key, in its order, with the prices it gives.
    assert view["models"] == ["model-1", "model-2"]
    assert view["prices"] == {"model-1": _PRICES, "model-2": _PRICES}
    assert [status["status"] for status in view["model_status"]] == ["ready", "ready"]
    # Nothing is chosen or limited per connection any more.
    assert not {
        "default_model",
        "is_default",
        "end_user_header",
        "user_concurrency",
        "user_daily_budget_microusd",
        "terms",
    } & set(view)
    # Listed and verified with exactly what would be saved, before anything was.
    assert admin.listed == [{"base_url": "https://provider.example/v1", "api_key": _KEY}]
    assert admin.verified == [
        {
            "base_url": "https://provider.example/v1",
            "api_key": _KEY,
            "models": ["model-1", "model-2"],
        }
    ]
    # The key is stored encrypted, in the provided connections' own domain, and answered nowhere.
    stored = admin.repository.rows[view["id"]]["api_key_ciphertext"]
    assert _KEY not in stored and operator_connection_cipher().decrypt(stored) == _KEY
    assert _KEY not in response.text and "api_key" not in view
    listed = await admin.call("GET")
    assert _KEY not in listed.text and "ciphertext" not in listed.text
    assert admin.actions() == ["agent.operator_model_connection.create"]
    assert admin.audit[0]["details"]["models"] == ["model-1", "model-2"]
    assert _KEY not in json.dumps(admin.audit, default=str)


async def test_a_model_listed_without_prices_is_kept_and_recorded_by_tokens(admin) -> None:
    admin.listing = [ListedModel("model-1", _PRICING), ListedModel("model-2", None)]
    view = await _created(admin)
    assert view["models"] == ["model-1", "model-2"]
    assert view["prices"] == {"model-1": _PRICES}


async def test_a_model_no_agent_can_use_is_kept_and_offered_to_nobody(admin) -> None:
    admin.results["model-2"] = _FAILED
    view = await _created(admin)
    assert view["models"] == ["model-1", "model-2"]
    statuses = {status["model"]: status["status"] for status in view["model_status"]}
    assert statuses == {"model-1": "ready", "model-2": "failed"}


async def test_a_key_none_of_whose_models_an_agent_can_use_saves_nothing(admin) -> None:
    admin.results["model-1"] = admin.results["model-2"] = _FAILED
    response = await admin.create()
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["type"] == "model_connection_verification_failed"
    assert {status["model"]: status["status"] for status in error["models"]} == {
        "model-1": "failed",
        "model-2": "failed",
    }
    assert admin.repository.rows == {} and admin.audit == []


@pytest.mark.parametrize(
    ("listing", "error_type"),
    [
        (CheckStop("auth", http_status=401), "operator_model_listing_failed"),
        (CheckStop("endpoint_missing", http_status=404), "operator_model_listing_failed"),
        ([], "operator_model_listing_empty"),
        (
            _listing(*(f"model-{n}" for n in range(MAX_MODELS + 1))),
            "operator_model_listing_too_long",
        ),
    ],
)
async def test_a_listing_that_names_no_usable_set_saves_nothing(admin, listing, error_type) -> None:
    admin.listing = listing
    response = await admin.create()
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["type"] == error_type
    assert _KEY not in response.text
    # Refused before any model was verified.
    assert admin.verified == [] and admin.repository.rows == {} and admin.audit == []


async def test_a_listing_of_exactly_the_most_models_is_saved(admin) -> None:
    admin.listing = _listing(*(f"model-{n}" for n in range(MAX_MODELS)))
    assert len((await _created(admin))["models"]) == MAX_MODELS


async def test_a_verification_that_outlasts_its_bound_saves_nothing(admin) -> None:
    admin.timeout = True
    assert (await admin.create()).status_code == 504
    assert admin.repository.rows == {}


@pytest.mark.parametrize(
    "fields",
    [
        {"base_url": "http://provider.example/v1"},
        {"base_url": "https://provider.example:8443/v1"},
        {"api_key": "has space"},
        # What an administrator no longer chooses is refused, not ignored.
        {"models": ["model-1"]},
        {"default_model": "model-1"},
        {"end_user_header": "X-End-User"},
        {"user_concurrency": 4},
        {"user_daily_budget_microusd": 5_000_000},
        {"unknown": 1},
    ],
)
async def test_what_could_not_be_a_connection_is_refused_before_any_provider_call(
    admin, fields
) -> None:
    response = await admin.create(**fields)
    assert response.status_code == 422
    assert response.json()["error"]["type"] == "invalid_model_connection"
    # Which field, never what was sent.
    assert _KEY not in response.text and "has space" not in response.text
    assert admin.listed == [] and admin.verified == [] and admin.repository.rows == {}


async def test_a_taken_name_is_refused_before_any_provider_call(admin) -> None:
    await _created(admin)
    admin.listed.clear()
    admin.verified.clear()
    response = await admin.create(name="provider")
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["type"] == "operator_model_connection_name_taken"
    assert admin.listed == [] and admin.verified == []


async def test_the_master_switch_stops_saving_and_enabling(admin, monkeypatch) -> None:
    view = await _created(admin)
    monkeypatch.setenv("AGENT_MODEL_CONNECTIONS_ENABLED", "false")
    assert (await admin.create(name="Another")).status_code == 403
    assert (await admin.call("PATCH", f"/{view['id']}", json={"enabled": True})).status_code == 403
    assert (await admin.call("POST", f"/{view['id']}/refresh-models")).status_code == 403
    listed = (await admin.call("GET")).json()
    assert listed["feature_enabled"] is False
    # Removing it still works.
    assert (await admin.call("DELETE", f"/{view['id']}")).status_code == 204


async def test_a_new_connection_is_enabled_with_nothing_else_to_set(admin) -> None:
    view = await _created(admin)
    response = await admin.call("PATCH", f"/{view['id']}", json={"enabled": True})
    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert admin.actions()[-1] == "agent.operator_model_connection.enable"
    # Enabling reads nothing from the provider.
    assert len(admin.listed) == len(admin.verified) == 1


# -- editing -----------------------------------------------------------------------


async def test_a_rename_calls_no_provider_and_closes_nothing_it_need_not(admin) -> None:
    view = await _enabled(admin)
    response = await admin.call(
        "PATCH", f"/{view['id']}", json={"name": "Renamed", "base_url": view["base_url"]}
    )
    assert response.json()["name"] == "Renamed"
    assert admin.listed == [] and admin.verified == []
    assert admin.repository.rows[view["id"]]["credential_revision"] == 1
    assert admin.audit[-1]["action"] == "agent.operator_model_connection.update"
    assert admin.audit[-1]["details"]["changed"] == ["name"]


async def test_rotating_the_key_follows_its_listing_and_keeps_running_processes(admin) -> None:
    view = await _enabled(admin)
    # The new key lists one more model, and one fewer.
    admin.listing = _listing("model-2", "model-3")
    response = await admin.call(
        "PATCH", f"/{view['id']}", json={"api_key": "sk-new-deployment-key"}
    )
    assert response.status_code == 200
    assert admin.listed == [
        {"base_url": "https://provider.example/v1", "api_key": "sk-new-deployment-key"}
    ]
    # A new key verifies every model it lists.
    assert admin.verified == [
        {
            "base_url": "https://provider.example/v1",
            "api_key": "sk-new-deployment-key",
            "models": ["model-2", "model-3"],
        }
    ]
    view = response.json()
    assert view["models"] == ["model-2", "model-3"]
    assert view["prices"] == {"model-2": _PRICES, "model-3": _PRICES}
    row = admin.repository.rows[view["id"]]
    assert (
        operator_connection_cipher().decrypt(row["api_key_ciphertext"]) == "sk-new-deployment-key"
    )
    # No process holds the key, so the revision a residency is bound to does not move.
    assert row["credential_revision"] == 1 and row["config_revision"] == 2
    assert admin.actions() == [
        "agent.operator_model_connection.rotate_key",
        "agent.operator_model_connection.update",
    ]
    assert admin.audit[0]["details"]["api_key"] == "[redacted]"
    assert admin.audit[1]["details"]["changed"] == ["models"]
    assert "sk-new-deployment-key" not in json.dumps(admin.audit, default=str)
    assert "sk-new-deployment-key" not in response.text


async def test_re_entering_the_stored_key_rotates_nothing(admin) -> None:
    view = await _enabled(admin)
    response = await admin.call("PATCH", f"/{view['id']}", json={"api_key": _KEY})
    assert response.status_code == 200
    assert admin.listed == [] and admin.verified == [] and admin.audit == []


async def test_a_new_base_url_is_listed_with_the_stored_key_and_closes_processes(admin) -> None:
    view = await _enabled(admin)
    response = await admin.call(
        "PATCH", f"/{view['id']}", json={"base_url": "https://other.example/v1"}
    )
    assert response.status_code == 200
    assert admin.listed == [{"base_url": "https://other.example/v1", "api_key": _KEY}]
    assert admin.verified[0]["base_url"] == "https://other.example/v1"
    assert admin.verified[0]["api_key"] == _KEY
    assert admin.repository.rows[view["id"]]["credential_revision"] == 2
    assert admin.repository.released == [view["id"]]


async def test_a_key_whose_listing_cannot_be_read_changes_nothing(admin) -> None:
    view = await _enabled(admin)
    before = json.dumps(admin.repository.rows[view["id"]], default=str, sort_keys=True)
    admin.listing = CheckStop("auth", http_status=401)
    response = await admin.call("PATCH", f"/{view['id']}", json={"api_key": "sk-wrong-key"})
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["type"] == "operator_model_listing_failed"
    assert json.dumps(admin.repository.rows[view["id"]], default=str, sort_keys=True) == before
    assert admin.verified == [] and admin.audit == []


@pytest.mark.parametrize("field", ["models", "default_model", "is_default"])
async def test_an_edit_cannot_choose_models_or_a_default(admin, field) -> None:
    view = await _created(admin)
    value = ["model-1"] if field == "models" else "model-1" if field == "default_model" else True
    response = await admin.call("PATCH", f"/{view['id']}", json={field: value})
    assert response.status_code == 422


async def test_an_edit_overtaken_by_another_saves_nothing(admin) -> None:
    view = await _enabled(admin)
    admin.repository.moved_during_verification = True
    response = await admin.call(
        "PATCH", f"/{view['id']}", json={"api_key": "sk-new-deployment-key"}
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["type"] == "operator_model_connection_changed"
    assert admin.audit == []


# -- refreshing --------------------------------------------------------------------


async def test_refreshing_follows_the_listing_and_verifies_only_what_it_added(admin) -> None:
    view = await _enabled(admin)
    admin.listing = [
        ListedModel("model-1", {"prompt": "1", "completion": "2"}),
        ListedModel("model-3", _PRICING),
    ]
    response = await admin.call("POST", f"/{view['id']}/refresh-models")
    assert response.status_code == 200, response.text
    # With the stored key, at the stored Base URL.
    assert admin.listed == [{"base_url": "https://provider.example/v1", "api_key": _KEY}]
    assert admin.verified == [
        {"base_url": "https://provider.example/v1", "api_key": _KEY, "models": ["model-3"]}
    ]
    refreshed = response.json()
    assert refreshed["models"] == ["model-1", "model-3"]
    statuses = {status["model"]: status["status"] for status in refreshed["model_status"]}
    assert statuses == {"model-1": "ready", "model-3": "ready"}
    # Every model's prices are read again; a removed model's go with it.
    assert refreshed["prices"] == {
        "model-1": {
            "input_price": 1_000_000,
            "output_price": 2_000_000,
            "cache_read_price": None,
            "cache_write_price": None,
        },
        "model-3": _PRICES,
    }
    assert refreshed["enabled"] is True
    # The key and the Base URL did not change, so no running process is closed.
    row = admin.repository.rows[view["id"]]
    assert (row["credential_revision"], row["config_revision"]) == (1, 1)
    assert admin.actions() == ["agent.operator_model_connection.update"]
    assert admin.audit[0]["details"]["changed"] == ["models"]


async def test_refreshing_an_unchanged_listing_changes_nothing_but_prices(admin) -> None:
    view = await _enabled(admin)
    admin.listing = _listing("model-1", "model-2", pricing=None)
    response = await admin.call("POST", f"/{view['id']}/refresh-models")
    assert response.status_code == 200
    assert response.json()["models"] == ["model-1", "model-2"]
    assert response.json()["prices"] == {}
    assert admin.verified == [] and admin.audit == []


async def test_a_refresh_whose_listing_fails_changes_nothing(admin) -> None:
    view = await _enabled(admin)
    before = json.dumps(admin.repository.rows[view["id"]], default=str, sort_keys=True)
    admin.listing = []
    response = await admin.call("POST", f"/{view['id']}/refresh-models")
    assert response.status_code == 422
    assert json.dumps(admin.repository.rows[view["id"]], default=str, sort_keys=True) == before
    assert set(admin.repository.prices) == {(view["id"], "model-1"), (view["id"], "model-2")}
    assert admin.audit == []


# -- disabling and deleting --------------------------------------------------------


async def test_disabling_and_deleting_close_running_processes(admin) -> None:
    view = await _enabled(admin)
    disabled = await admin.call("PATCH", f"/{view['id']}", json={"enabled": False})
    assert disabled.json()["enabled"] is False
    assert admin.repository.rows[view["id"]]["credential_revision"] == 2
    deleted = await admin.call("DELETE", f"/{view['id']}")
    assert deleted.status_code == 204
    assert admin.repository.released == [view["id"], view["id"]]
    # The key is erased with it, and the id reads as gone from then on.
    assert admin.repository.rows[view["id"]]["api_key_ciphertext"] is None
    assert (await admin.call("PATCH", f"/{view['id']}", json={"name": "Back"})).status_code == 404
    assert (await admin.call("GET")).json()["connections"] == []
    assert admin.actions() == [
        "agent.operator_model_connection.disable",
        "agent.operator_model_connection.delete",
    ]


@pytest.mark.parametrize("connection_id", ["mconn_" + "0" * 32, "oconn_" + "9" * 32, "x"])
async def test_an_unknown_connection_is_not_found(admin, connection_id) -> None:
    for method, path, body in (
        ("PATCH", "", {"name": "X"}),
        ("DELETE", "", None),
        ("POST", "/refresh-models", None),
        ("GET", "/usage", None),
    ):
        response = await admin.call(method, f"/{connection_id}{path}", json=body)
        assert response.status_code == 404, (method, path)
    assert admin.listed == []


async def test_the_list_says_whether_any_host_reaches_the_relay(admin) -> None:
    await _created(admin)
    assert (await admin.call("GET")).json()["relay_hosts"] == 1
    admin.repository.relay_hosts = 0
    assert (await admin.call("GET")).json()["relay_hosts"] == 0


_ACCOUNT = "01HZXACCOUNTAAAAAAAAAAAAAAA"


# -- usage -------------------------------------------------------------------------


async def test_usage_is_requests_tokens_and_cost_per_day_account_and_model(admin) -> None:
    view = await _created(admin)
    used = {
        "requests": 42,
        "cost_microusd": 1_250_000,
        "input_tokens": 400_000,
        "cache_read_tokens": 300_000,
        "cache_write_tokens": 0,
        "output_tokens": 9_000,
        "estimated_requests": 2,
    }
    admin.repository.usage_rows = [
        {"day": date(2026, 11, 11), "user_id": _ACCOUNT, "model": "model-1", **used},
        {"day": date(2026, 10, 1), "user_id": _ACCOUNT, "model": "model-1", **used},
    ]
    response = await admin.call(
        "GET", f"/{view['id']}/usage", params={"from": "2026-11-10", "to": "2026-11-12"}
    )
    assert response.json() == {
        "rows": [
            {
                "day": "2026-11-11",
                "account_id": _ACCOUNT,
                "email": "a@example.edu",
                "model": "model-1",
                **used,
            }
        ],
        "truncated": False,
    }


@pytest.mark.parametrize(
    "span",
    [{"from": "2026-11-12", "to": "2026-11-01"}, {"from": "2026-01-01", "to": "2026-11-12"}],
)
async def test_a_usage_range_is_bounded(admin, span) -> None:
    view = await _created(admin)
    assert (await admin.call("GET", f"/{view['id']}/usage", params=span)).status_code == 422
