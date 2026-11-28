"""Signing in at the configured model provider for a key, from the start route to the save.

The provider is stood in for at both ends: its authorization page is only a
URL the start route answers, and its key URL is an ``httpx.MockTransport``
behind the real exchange. The repository and the OAuth states are in-memory
stand-ins with the ports' semantics, and model verification is the fake every
connection test uses. What is pinned: the state and the verifier are bound to
the owner and work once; everything that can be refused without a key is
refused before the code is exchanged; the key is stored only as ciphertext and
never answered; and the connection lists exactly the configured models an
agent can use.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from tests.servers.test_model_connections_api import (  # noqa: F401 -- autouse fixtures
    _UNUSABLE,
    FakeModelConnections,
    _encryption,
    _facts,
    _Provider,
)

from cloud_agent import settings
from cloud_agent.api import model_connections as connections_api, model_sign_in as api
from cloud_agent.api._owner_auth import require_agent_owner
from cloud_agent.api.deps import get_storage
from cloud_agent.application import model_connection_check as check, model_sign_in as flow
from cloud_agent.application.model_connections import model_connection_cipher
from cloud_agent.source_control.source_control import SourceControlCipher

START = "/v1/agent/model-sign-in"
FINISH = "/v1/agent/model-sign-in/finish"
_CIPHER_SECRET = "model-connection-test-encryption-key-0123456789"
_ISSUED_KEY = "pk-issued-for-the-owner-0123456789abcdef"
_WEB = "https://agents.example.org"
_PROVIDER = {
    "model_sign_in.name": "Example Inference",
    "model_sign_in.authorize_url": "https://console.provider.example/authorize/key",
    "model_sign_in.key_url": "https://console.provider.example/v1/auth/keys",
    "model_sign_in.base_url": "https://api.provider.example/v1/",
    "model_sign_in.models": "model-a, model-b",
}


def _configure(**overrides: str) -> None:
    settings.apply_settings(
        1,
        settings.encode_changes(
            {
                "web_base_url": _WEB,
                "source_control_encryption_key": _CIPHER_SECRET,
                **_PROVIDER,
                **overrides,
            }
        ),
    )


@pytest.fixture(autouse=True)
def verifier(monkeypatch) -> _Provider:
    """Every verification answers Chat Completions working, unless a test says otherwise."""
    fake = _Provider()
    monkeypatch.setattr(check, "verify_model", fake)
    return fake


@pytest.fixture(autouse=True)
def configured(monkeypatch):
    monkeypatch.setenv("AGENT_SETTINGS_ENCRYPTION_KEY", "settings-root-key-for-sign-in-tests-0123")
    _configure()
    yield
    settings.reset_settings()


class FakeOAuthStates:
    """``agent_oauth_states`` in memory: hashed, owner- and provider-bound, single use."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create_oauth_state(
        self, *, state_hash, user_id, provider, code_verifier_ciphertext, expires_at
    ):
        self.rows[state_hash] = {
            "user_id": user_id,
            "provider": provider,
            "code_verifier_ciphertext": code_verifier_ciphertext,
            "expires_at": expires_at,
            "consumed_at": None,
        }

    async def consume_oauth_state(self, *, state_hash, user_id, provider):
        row = self.rows.get(state_hash)
        if (
            row is None
            or row["user_id"] != user_id
            or row["provider"] != provider
            or row["consumed_at"] is not None
            or row["expires_at"] <= datetime.now(UTC)
        ):
            return None
        row["consumed_at"] = datetime.now(UTC)
        return copy.deepcopy(row)


class _Store:
    def __init__(self) -> None:
        self.model_connections = FakeModelConnections()
        self.source_control = FakeOAuthStates()
        self.host_pool = None


class KeyEndpoint:
    """The provider's key URL: records each exchange and answers as told."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.status = 200
        self.body: Any = {"key": _ISSUED_KEY, "user_id": "provider-user-7"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append({"url": str(request.url), "json": json.loads(request.content)})
        if isinstance(self.body, bytes):
            return httpx.Response(self.status, content=self.body)
        return httpx.Response(self.status, json=self.body)


@pytest.fixture(autouse=True)
def key_endpoint(monkeypatch) -> KeyEndpoint:
    """The exchange dials this endpoint instead of the network; the address check passes."""
    endpoint = KeyEndpoint()

    async def public(url, *, resolve=None):
        return None

    monkeypatch.setattr(flow, "guard_base_url", public)
    monkeypatch.setattr(
        flow, "pinned_transport", lambda: httpx.MockTransport(endpoint.handler), raising=True
    )
    return endpoint


def _app(store: _Store, owner: str = "owner-1") -> FastAPI:
    app = FastAPI()
    app.include_router(api.router)
    app.include_router(connections_api.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[require_agent_owner] = lambda: {"user_id": owner}
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _start(store: _Store, owner: str = "owner-1") -> dict[str, str]:
    """Start a sign-in; answer the parameters the provider's page received."""
    async with await _client(_app(store, owner)) as client:
        response = await client.post(START)
    assert response.status_code == 200, response.text
    assert response.headers["Cache-Control"] == "no-store"
    url = urlsplit(response.json()["authorization_url"])
    assert f"{url.scheme}://{url.netloc}{url.path}" == _PROVIDER["model_sign_in.authorize_url"]
    params = {key: values[0] for key, values in parse_qs(url.query).items()}
    callback = urlsplit(params["callback_url"])
    assert f"{callback.scheme}://{callback.netloc}{callback.path}" == (
        f"{_WEB}/settings/models/connected"
    )
    params["state"] = parse_qs(callback.query)["state"][0]
    return params


async def _finish(store: _Store, state: str, code: str = "code-1", owner: str = "owner-1"):
    async with await _client(_app(store, owner)) as client:
        return await client.post(FINISH, json={"code": code, "state": state})


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def test_start_binds_a_hashed_state_and_an_s256_challenge_to_the_owner():
    store = _Store()
    params = await _start(store)

    assert params["code_challenge_method"] == "S256"
    assert set(params) == {"callback_url", "code_challenge", "code_challenge_method", "state"}
    [(state_hash, row)] = store.source_control.rows.items()
    # Stored hashed, for this owner and this flow alone, never as sent.
    assert state_hash == hashlib.sha256(params["state"].encode()).hexdigest()
    assert row["user_id"] == "owner-1"
    assert row["provider"] == "model-sign-in"
    verifier = SourceControlCipher(domain=b"cloud-agent/model-sign-in-verifier/v1").decrypt(
        row["code_verifier_ciphertext"]
    )
    assert verifier not in json.dumps(params)
    assert params["code_challenge"] == _challenge(verifier)


async def test_finish_exchanges_the_code_with_the_verifier_and_saves_a_verified_connection(
    key_endpoint, verifier
):
    store = _Store()
    params = await _start(store)
    response = await _finish(store, params["state"])

    assert response.status_code == 201, response.text
    assert response.headers["Cache-Control"] == "no-store"
    [request] = key_endpoint.requests
    assert request["url"] == _PROVIDER["model_sign_in.key_url"]
    assert request["json"]["code"] == "code-1"
    assert request["json"]["code_challenge_method"] == "S256"
    assert _challenge(request["json"]["code_verifier"]) == params["code_challenge"]

    body = response.json()
    assert _ISSUED_KEY not in response.text
    assert body["skipped_models"] == []
    view = body["connection"]
    assert view["name"] == "Example Inference"
    assert view["base_url"] == "https://api.provider.example/v1"
    assert view["models"] == ["model-a", "model-b"]
    assert [status["status"] for status in view["model_status"]] == ["ready", "ready"]
    # Verified with the issued key, one hourly check per model.
    assert {(call["api_key"], call["model"]) for call in verifier.calls} == {
        (_ISSUED_KEY, "model-a"),
        (_ISSUED_KEY, "model-b"),
    }
    assert store.model_connections.checks == ["owner-1", "owner-1"]
    stored = store.model_connections.rows[view["id"]]
    assert _ISSUED_KEY not in str({k: v for k, v in stored.items() if k != "api_key_ciphertext"})
    assert model_connection_cipher().decrypt(stored["api_key_ciphertext"]) == _ISSUED_KEY

    # Signed in now: neither the Models page nor a second start offers it again.
    async with await _client(_app(store)) as client:
        listed = (await client.get("/v1/agent/model-connections")).json()
        again = await client.post(START)
    assert listed["sign_in"] is None
    assert again.status_code == 409
    assert again.json()["detail"]["error"]["type"] == "model_sign_in_connected"


async def test_a_state_works_once_and_only_for_the_owner_it_was_issued_to(key_endpoint):
    store = _Store()
    params = await _start(store)

    other = await _finish(store, params["state"], owner="owner-2")
    assert other.status_code == 400
    assert other.json()["detail"]["error"]["type"] == "model_sign_in_expired"
    # The other owner's attempt did not spend it.
    assert (await _finish(store, params["state"])).status_code == 201
    replay = await _finish(store, params["state"])
    assert replay.status_code == 400
    assert replay.json()["detail"]["error"]["type"] == "model_sign_in_expired"
    assert len(key_endpoint.requests) == 1


async def test_an_unknown_state_exchanges_nothing(key_endpoint):
    response = await _finish(_Store(), "not-a-state")

    assert response.status_code == 400
    assert response.json()["detail"]["error"]["type"] == "model_sign_in_expired"
    assert key_endpoint.requests == []


@pytest.mark.parametrize(
    ("status", "body", "expected", "kind"),
    [
        (400, {"error": "invalid_grant"}, 400, "model_sign_in_refused"),
        (403, {"error": "forbidden"}, 400, "model_sign_in_refused"),
        (500, {"error": "boom"}, 503, "model_sign_in_unavailable_upstream"),
        (429, {"error": "slow down"}, 503, "model_sign_in_unavailable_upstream"),
        (302, {}, 503, "model_sign_in_unavailable_upstream"),
        (200, {"no": "key"}, 503, "model_sign_in_unavailable_upstream"),
        (200, {"key": "two words"}, 503, "model_sign_in_unavailable_upstream"),
        (200, b"not json", 503, "model_sign_in_unavailable_upstream"),
        (200, b"x" * (64 * 1024 + 1), 503, "model_sign_in_unavailable_upstream"),
    ],
)
async def test_an_exchange_without_a_usable_key_saves_nothing(
    key_endpoint, verifier, status, body, expected, kind
):
    store = _Store()
    params = await _start(store)
    key_endpoint.status = status
    key_endpoint.body = body

    response = await _finish(store, params["state"])

    assert response.status_code == expected, response.text
    assert response.json()["detail"]["error"]["type"] == kind
    assert store.model_connections.rows == {}
    assert verifier.calls == []


async def test_models_no_agent_can_use_are_left_off_and_named(verifier):
    verifier.answers["model-b"] = _UNUSABLE
    store = _Store()
    params = await _start(store)

    response = await _finish(store, params["state"])

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["connection"]["models"] == ["model-a"]
    assert body["skipped_models"] == ["model-b"]
    stored = next(iter(store.model_connections.rows.values()))
    assert set(stored["model_checks"]) == {"model-a"}


async def test_a_key_no_model_verifies_with_saves_nothing_and_says_where_the_key_is(verifier):
    verifier.answers["model-a"] = _UNUSABLE
    verifier.answers["model-b"] = _facts("failed", "failed", "failed")
    store = _Store()
    params = await _start(store)

    response = await _finish(store, params["state"])

    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["type"] == "model_connection_verification_failed"
    assert "Example Inference issued a key" in error["message"]
    assert [model["model"] for model in error["models"]] == ["model-a", "model-b"]
    assert store.model_connections.rows == {}


async def test_models_on_another_connection_are_skipped_before_the_exchange(key_endpoint, verifier):
    store = _Store()
    await store.model_connections.create_model_connection(
        user_id="owner-1",
        values={
            "id": "mconn_" + "1" * 32,
            "name": "Elsewhere",
            "base_url": "https://other.example/v1",
            "models": ["model-a"],
            "api_key_ciphertext": model_connection_cipher().encrypt("sk-other"),
            "model_checks": {"model-a": _facts()},
        },
    )
    params = await _start(store)

    response = await _finish(store, params["state"])

    assert response.status_code == 201, response.text
    assert response.json()["connection"]["models"] == ["model-b"]
    assert response.json()["skipped_models"] == ["model-a"]
    assert [call["model"] for call in verifier.calls] == ["model-b"]


async def test_every_model_taken_refuses_before_a_key_is_issued(key_endpoint):
    store = _Store()
    await store.model_connections.create_model_connection(
        user_id="owner-1",
        values={
            "id": "mconn_" + "1" * 32,
            "name": "Elsewhere",
            "base_url": "https://other.example/v1",
            "models": ["model-a", "model-b"],
            "api_key_ciphertext": model_connection_cipher().encrypt("sk-other"),
            "model_checks": {"model-a": _facts(), "model-b": _facts()},
        },
    )
    params = await _start(store)

    response = await _finish(store, params["state"])

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["type"] == "model_sign_in_models_taken"
    assert key_endpoint.requests == []


async def test_the_hourly_check_limit_refuses_before_a_key_is_issued(key_endpoint):
    store = _Store()
    params = await _start(store)
    store.model_connections.checks = ["owner-1"] * 19

    response = await _finish(store, params["state"])

    assert response.status_code == 429
    assert response.json()["detail"]["error"]["type"] == "model_connection_check_limit"
    assert key_endpoint.requests == []


async def test_a_connection_made_meanwhile_refuses_before_a_key_is_issued(key_endpoint):
    store = _Store()
    params = await _start(store)
    await store.model_connections.create_model_connection(
        user_id="owner-1",
        values={
            "id": "mconn_" + "2" * 32,
            "name": "Typed by hand",
            "base_url": "https://api.provider.example/v1",
            "models": ["model-z"],
            "api_key_ciphertext": model_connection_cipher().encrypt("sk-typed"),
            "model_checks": {"model-z": _facts()},
        },
    )

    response = await _finish(store, params["state"])

    assert response.status_code == 409
    assert response.json()["detail"]["error"]["type"] == "model_sign_in_connected"
    assert "Typed by hand" in response.json()["detail"]["error"]["message"]
    assert key_endpoint.requests == []


async def test_a_taken_name_gets_the_next_number():
    store = _Store()
    await store.model_connections.create_model_connection(
        user_id="owner-1",
        values={
            "id": "mconn_" + "3" * 32,
            "name": "example inference",
            "base_url": "https://other.example/v1",
            "models": ["model-z"],
            "api_key_ciphertext": model_connection_cipher().encrypt("sk-other"),
            "model_checks": {"model-z": _facts()},
        },
    )
    params = await _start(store)

    response = await _finish(store, params["state"])

    assert response.status_code == 201, response.text
    assert response.json()["connection"]["name"] == "Example Inference 2"


async def test_nothing_is_offered_or_started_until_every_setting_is_filled_in():
    settings.reset_settings()
    _configure(**{"model_sign_in.models": ""})
    store = _Store()
    async with await _client(_app(store)) as client:
        listed = (await client.get("/v1/agent/model-connections")).json()
        started = await client.post(START)
    assert listed["sign_in"] is None
    assert started.status_code == 404
    assert started.json()["detail"]["error"]["type"] == "model_sign_in_unavailable"
    assert store.source_control.rows == {}


async def test_the_models_page_offers_the_provider_by_name_and_base_url():
    async with await _client(_app(_Store())) as client:
        listed = (await client.get("/v1/agent/model-connections")).json()
    assert listed["sign_in"] == {
        "name": "Example Inference",
        "base_url": "https://api.provider.example/v1",
    }


async def test_switched_off_connections_offer_and_start_nothing(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_CONNECTIONS_ENABLED", "false")
    store = _Store()
    async with await _client(_app(store)) as client:
        listed = (await client.get("/v1/agent/model-connections")).json()
        started = await client.post(START)
    assert listed["sign_in"] is None
    assert started.status_code == 403
    assert started.json()["detail"]["error"]["type"] == "model_connections_disabled"


async def test_a_finish_body_is_refused_without_echoing_it():
    async with await _client(_app(_Store())) as client:
        response = await client.post(FINISH, json={"code": "secret-code", "extra": "x"})
    assert response.status_code == 422
    assert "secret-code" not in response.text
