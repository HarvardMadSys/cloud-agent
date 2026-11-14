"""The model relay, end to end: a placeholder in, the deployment's key out, or nothing at all."""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from cloud_agent.api import model_relay
from cloud_agent.api.deps import get_storage
from cloud_agent.application.model_connection_stream import CheckStop
from cloud_agent.application.model_connections import operator_connection_cipher
from cloud_agent.domain.model_usage import IMAGE_TOKEN_ESTIMATE, estimated_prompt_tokens
from cloud_agent.domain.operator_models import Allowance, ModelPrices, end_user_tag
from cloud_agent.ports.operator_models import ChargeOutcomeUnknown
from cloud_agent.tokens import (
    mint_residency_github_token,
    mint_residency_mcp_token,
    mint_residency_model_token,
)

pytestmark = pytest.mark.asyncio

_KEY = "sk-deployment-key-0123456789"
_ROOT = "model-relay-settings-root-key"
_CONNECTION = "oconn_" + "c" * 32
#: $2 in, $10 out and $0.50 for cached input, per million tokens.
_PRICES = ModelPrices(input=2_000_000, output=10_000_000, cache_read=500_000)
#: What the provider says a request used unless a test scripts another answer,
#: and what that costs at ``_PRICES``, in millionths of a dollar.
_USAGE = {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100}
_ANSWER = {"id": "answer", "usage": _USAGE}
_COST = 3000
_BUDGET = ("budget", _CONNECTION, "user_1")


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("AGENT_CONTROL_TOKEN_SECRET", "model-relay-test-secret")
    monkeypatch.setenv("AGENT_SETTINGS_ENCRYPTION_KEY", _ROOT)


class _Store:
    """A running turn of ``athr_1`` (residency 2) by ``user_1`` on one provided connection."""

    def __init__(
        self,
        *,
        running: bool = True,
        budget: int = 5_000_000,
        spent: int = 0,
        offered: bool = True,
        prices: ModelPrices | None = _PRICES,
    ) -> None:
        self.running = running
        self.budget, self.spent, self.offered, self.prices = budget, spent, offered, prices
        self.charges: list[dict[str, Any]] = []
        #: The day each charge was written for.
        self.days: list[Any] = []
        #: What the next writes raise, one each, before they succeed.
        self.write_failures: list[BaseException] = []
        self.key_refused: list[bool] = []
        self.global_concurrency: int | None = None
        self.bound_revision: int | None = 3
        self.on_other_connection = False
        self.connection: dict[str, Any] | None = {
            "id": _CONNECTION,
            "name": "Provider",
            "base_url": "https://provider.example/v1",
            "models": ["model-1", "model-2"],
            "enabled": True,
            "deleted_at": None,
            "credential_revision": 3,
            "api_key_ciphertext": operator_connection_cipher().encrypt(_KEY),
            "end_user_header": None,
            "user_concurrency": 4,
            "key_refused_at": None,
        }
        self.operator_models = self

    async def resolve_model_relay_caller(self, *, thread_id, residency_generation, model):
        if not self.running or (thread_id, residency_generation) != ("athr_1", 2):
            return None
        connection = None if self.on_other_connection else self.connection
        listed = connection is not None and model in connection["models"]
        allowance = Allowance(
            offered=self.offered, budget_microusd=self.budget, spent_microusd=self.spent
        )
        return {
            "job_id": "ajob_1",
            "attempt_id": 1,
            "thread_id": thread_id,
            "user_id": "user_1",
            "model_connection_id": _CONNECTION if connection else "mconn_" + "0" * 32,
            "bound_revision": self.bound_revision if connection else None,
            "connection": connection,
            "allowance": allowance if listed else None,
            "prices": self.prices if listed else None,
            "global_concurrency": self.global_concurrency,
        }

    async def record_operator_request(self, *, connection_id, user_id, day, **charge):
        assert (connection_id, user_id) == (_CONNECTION, "user_1")
        if self.write_failures:
            raise self.write_failures.pop(0)
        self.days.append(day)
        self.charges.append(charge)
        self.spent += charge["cost_microusd"]

    async def mark_operator_key_refused(self, *, connection_id, refused):
        self.key_refused.append(refused)


def _charge(
    cost: int, *, prompt: int, output: int, cached: int = 0, written: int = 0, estimated=False
) -> dict[str, Any]:
    """One recorded request on ``model-1``."""
    return {
        "model": "model-1",
        "cost_microusd": cost,
        "input_tokens": prompt,
        "cache_read_tokens": cached,
        "cache_write_tokens": written,
        "output_tokens": output,
        "estimated": estimated,
    }


def _estimate(body: bytes, *, output: int = 0) -> dict[str, Any]:
    """What a request with this body is charged when its answer named no usage."""
    tokens = estimated_prompt_tokens(json.loads(body))
    cost = (tokens * _PRICES.input + output * _PRICES.output + 500_000) // 1_000_000
    return _charge(cost, prompt=tokens, output=output, estimated=True)


class _Provider:
    """The provider, answering every request as scripted and recording what it got."""

    def __init__(
        self,
        status: int = 200,
        body: Any = None,
        *,
        fail: bool = False,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.status, self.fail = status, fail
        self.body = body if body is not None else _ANSWER
        self.headers = headers or {}

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(await request.aread())
        if self.fail:
            raise httpx.ConnectError("no route", request=request)
        # A stream, as from the network: the relay streams it back unread.
        return httpx.Response(
            self.status,
            headers={
                "content-type": "application/json",
                "x-request-id": "req_1",
                **self.headers,
            },
            stream=httpx.ByteStream(json.dumps(self.body).encode()),
        )


def _placeholder(generation: int = 2) -> str:
    return mint_residency_model_token(thread_id="athr_1", residency_generation=generation)


def _upstream(handle: Any) -> httpx.AsyncClient:
    """The listener's own client, dialling ``handle`` in place of the network."""
    return model_relay.relay_client(httpx.MockTransport(handle))


def _app(
    store: _Store | None,
    upstream: httpx.AsyncClient,
    *,
    slots: model_relay.Slots | None = None,
    reserved: model_relay.Reserved | None = None,
    pending: model_relay.PendingCharges | None = None,
    guard: Any = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(model_relay.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[model_relay.get_relay_client] = lambda: upstream
    held = slots or model_relay.Slots()
    app.dependency_overrides[model_relay.get_slots] = lambda: held
    kept = reserved or model_relay.Reserved()
    app.dependency_overrides[model_relay.get_reserved] = lambda: kept
    later = pending or model_relay.PendingCharges(kept, delays=(0.0,))
    app.dependency_overrides[model_relay.get_pending] = lambda: later

    async def allowed(_base_url: str) -> None:
        return None

    app.dependency_overrides[model_relay.get_endpoint_guard] = lambda: guard or allowed
    return app


async def _send(
    store: _Store | None,
    provider: _Provider,
    method: str = "POST",
    path: str = "/v1/chat/completions",
    *,
    bearer: str | None = "",
    slots: model_relay.Slots | None = None,
    reserved: model_relay.Reserved | None = None,
    pending: model_relay.PendingCharges | None = None,
    guard: Any = None,
    upstream: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """One request to the relay; ``upstream`` is a client several requests share."""
    client = upstream or _upstream(provider.handle)
    app = _app(store, client, slots=slots, reserved=reserved, pending=pending, guard=guard)
    headers = dict(kwargs.pop("headers", {}))
    if bearer is not None:
        headers.setdefault("Authorization", f"Bearer {bearer or _placeholder()}")
    if method == "POST" and "json" not in kwargs and "content" not in kwargs:
        kwargs["json"] = {"model": "model-1", "messages": [{"role": "user", "content": "hi"}]}
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://relay") as caller:
            return await caller.request(method, path, headers=headers, **kwargs)
    finally:
        if upstream is None:
            await client.aclose()


class _Caller:
    """One CLI's connection to the relay, driven by hand so that it can leave.

    ``spec_version`` is the ASGI version the server speaks. Up to 2.3 (what
    uvicorn speaks) a streamed response is told its caller left; from 2.4 it
    finds out when its next send fails.
    """

    #: The request every caller sends.
    BODY = json.dumps({"model": "model-1", "messages": []}).encode()

    def __init__(
        self, app: FastAPI, *, spec_version: str = "2.3", path: str = "/v1/chat/completions"
    ) -> None:
        body = self.BODY
        self._unread = [{"type": "http.request", "body": body, "more_body": False}]
        self._tells = spec_version == "2.3"
        self.left = asyncio.Event()
        self.sent: list[dict[str, Any]] = []
        self.heard = asyncio.Event()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": spec_version},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "root_path": "",
            "server": ("relay", 80),
            "client": ("192.0.2.1", 40000),
            "headers": [
                (b"host", b"relay"),
                (b"authorization", f"Bearer {_placeholder()}".encode()),
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
        self.call = asyncio.ensure_future(app(scope, self._receive, self._send))

    async def _receive(self) -> dict[str, Any]:
        if self._unread:
            return self._unread.pop(0)
        if not self._tells:
            await asyncio.Event().wait()
        await self.left.wait()
        return {"type": "http.disconnect"}

    async def _send(self, message: dict[str, Any]) -> None:
        if self.left.is_set():
            # Dropped, as uvicorn does, or refused, as ASGI 2.4 has it.
            if self._tells:
                return
            raise OSError("the caller has left")
        self.sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            self.heard.set()

    async def ended(self) -> BaseException | None:
        """Wait for the relay to finish with this request; what it raised, if anything."""
        done, _ = await asyncio.wait({self.call}, timeout=5)
        assert done, "the relay is still serving a caller that left"
        return self.call.exception()


class _OpenStream(httpx.AsyncByteStream):
    """An answer that has begun and says nothing more until it is told to."""

    def __init__(self, *, then: BaseException | bytes | None = None) -> None:
        self.go = asyncio.Event()
        self.closed = False
        self._then = then

    async def __aiter__(self):
        yield b"data: first\n\n"
        await self.go.wait()
        if isinstance(self._then, BaseException):
            raise self._then
        if self._then:
            yield self._then

    async def aclose(self) -> None:
        self.closed = True


def _slots_held(slots: model_relay.Slots) -> int:
    return slots.held(("user", _CONNECTION, "user_1")) + slots.held(
        ("model", _CONNECTION, "model-1")
    )


# -- what is forwarded -----------------------------------------------------------


async def test_a_running_turns_request_goes_out_with_the_deployments_key() -> None:
    store, provider = _Store(), _Provider()
    response = await _send(store, provider)

    assert response.status_code == 200 and response.json() == _ANSWER
    assert response.headers["x-request-id"] == "req_1"
    [sent] = provider.requests
    assert str(sent.url) == "https://provider.example/v1/chat/completions"
    assert sent.headers["authorization"] == f"Bearer {_KEY}"
    assert "x-api-key" not in sent.headers
    # The body is the CLI's own, byte for byte.
    assert json.loads(provider.bodies[0])["messages"] == [{"role": "user", "content": "hi"}]
    # Charged what the answer said it used, at the model's prices.
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]
    # Neither the key nor the placeholder comes back.
    assert _KEY not in response.text and "amd." not in response.text


async def test_an_anthropic_client_keeps_its_own_way_of_carrying_a_key() -> None:
    store, provider = _Store(), _Provider()
    response = await _send(
        store,
        provider,
        path="/v1/messages",
        bearer=None,
        headers={"x-api-key": _placeholder(), "anthropic-version": "2023-06-01"},
        params={"beta": "true"},
    )
    assert response.status_code == 200
    [sent] = provider.requests
    assert str(sent.url) == "https://provider.example/v1/messages?beta=true"
    assert sent.headers["x-api-key"] == _KEY and "authorization" not in sent.headers
    assert sent.headers["anthropic-version"] == "2023-06-01"


async def test_only_the_headers_an_agent_needs_seen_go_on_to_the_provider() -> None:
    store, provider = _Store(), _Provider()
    kept = {
        "Accept": "text/event-stream",
        "Content-Type": "application/json",
        "User-Agent": "agent-cli/1.2",
        "Anthropic-Version": "2023-06-01",
        "Anthropic-Beta": "tools-2024-04-04",
        "OpenAI-Beta": "responses=v1",
    }
    # Each of these would be the sandbox telling the provider what to do with
    # the deployment's key, or who and where the request is from.
    dropped = {
        "OpenAI-Organization": "org-other",
        "OpenAI-Project": "proj_other",
        "Cookie": "session=abc",
        "X-Forwarded-For": "203.0.113.9",
        "Forwarded": "for=203.0.113.9",
        "HTTP-Referer": "https://app.example",
        "X-Title": "Another app",
        "Idempotency-Key": "another-owners-request",
        "Proxy-Authorization": "Basic abc",
        "X-Stainless-Lang": "js",
        "X-Anything-Else": "1",
    }
    response = await _send(store, provider, headers={**kept, **dropped})

    assert response.status_code == 200
    [sent] = provider.requests
    for name, value in kept.items():
        assert sent.headers[name] == value
    for name in dropped:
        assert name not in sent.headers
    # Nothing else goes out than those, the key, the relay's own answer to
    # which encodings it takes, and what its connection says of itself.
    assert set(sent.headers) == {name.lower() for name in kept} | {
        "authorization",
        "accept-encoding",
        "host",
        "content-length",
        "connection",
    }


@pytest.mark.parametrize("accepted", ["gzip, br, zstd", "identity", None])
async def test_the_provider_is_asked_for_no_encoding_whatever_the_client_takes(accepted) -> None:
    """The answer is read for its usage on the way back, so it has to arrive unencoded."""
    store, provider = _Store(), _Provider()
    seen: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return await provider.handle(request)

    upstream = _upstream(handle)
    try:
        if accepted is None:
            # Sent by hand: a test client adds an Accept-Encoding of its own.
            await _Caller(_app(store, upstream)).ended()
        else:
            await _send(store, provider, upstream=upstream, headers={"Accept-Encoding": accepted})
    finally:
        await upstream.aclose()
    [sent] = seen
    assert sent.headers["accept-encoding"] == "identity"


async def test_listing_models_and_counting_tokens_cost_nothing() -> None:
    # Served even to an owner whose budget is used up, and never charged.
    store, provider = _Store(budget=1000, spent=1000), _Provider()
    listed = await _send(store, provider, "GET", "/v1/models")
    counted = await _send(store, provider, path="/v1/messages/count_tokens")
    assert (listed.status_code, counted.status_code) == (200, 200)
    assert store.charges == []
    assert [str(request.url) for request in provider.requests] == [
        "https://provider.example/v1/models",
        "https://provider.example/v1/messages/count_tokens",
    ]


async def test_the_provider_is_told_who_only_by_an_opaque_tag() -> None:
    store, provider = _Store(), _Provider()
    assert store.connection is not None
    store.connection["end_user_header"] = "X-End-User"
    await _send(store, provider, headers={"X-End-User": "somebody-else"})
    [sent] = provider.requests
    # The relay's own, whatever the sandbox sent under that name.
    assert sent.headers["x-end-user"] == end_user_tag(_ROOT, "user_1")
    assert "user_1" not in sent.headers["x-end-user"]


async def test_the_provider_answers_and_errors_are_relayed_as_they_are() -> None:
    store = _Store()
    provider = _Provider(400, {"error": {"message": "context_length_exceeded"}})
    response = await _send(store, provider)
    assert response.status_code == 400
    assert response.json() == {"error": {"message": "context_length_exceeded"}}
    # An error is not an answer the owner pays for.
    assert store.charges == []


async def test_a_cookie_one_answer_sets_goes_out_with_no_later_request() -> None:
    """Every owner's requests share one client, which keeps nothing between them."""
    store = _Store()
    provider = _Provider(headers={"set-cookie": "session=first-caller; Path=/"})
    async with _upstream(provider.handle) as upstream:
        first = await _send(store, provider, upstream=upstream)
        second = await _send(store, provider, upstream=upstream)
    assert first.status_code == second.status_code == 200
    assert [request.headers.get("cookie") for request in provider.requests] == [None, None]


# -- who may use it --------------------------------------------------------------


@pytest.mark.parametrize(
    "credential",
    ["sk-an-owners-own-key", "not-a-token", "amd.forged.signature"],
)
async def test_anything_but_a_placeholder_is_refused_and_never_passed_on(credential) -> None:
    store, provider = _Store(), _Provider()
    response = await _send(store, provider, bearer=credential)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_credential"
    assert provider.requests == [] and store.charges == []


async def test_no_credential_is_refused() -> None:
    provider = _Provider()
    response = await _send(_Store(), provider, bearer=None)
    assert response.status_code == 401 and provider.requests == []


@pytest.mark.parametrize("mint", [mint_residency_mcp_token, mint_residency_github_token])
async def test_another_relays_credential_buys_nothing_here(mint) -> None:
    provider = _Provider()
    other = mint(thread_id="athr_1", residency_generation=2)
    response = await _send(_Store(), provider, bearer=other)
    assert response.status_code == 401 and provider.requests == []


async def test_a_stopped_turn_or_a_rotated_residency_buys_nothing() -> None:
    provider = _Provider()
    stopped = await _send(_Store(running=False), provider)
    rotated = await _send(_Store(), provider, bearer=_placeholder(generation=3))
    for response in (stopped, rotated):
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "invalid_credential"
    assert provider.requests == []


async def test_a_turn_on_the_owners_own_connection_cannot_borrow_the_deployments_key() -> None:
    store, provider = _Store(), _Provider()
    store.on_other_connection = True
    response = await _send(store, provider)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provided_unavailable"
    assert provider.requests == []


@pytest.mark.parametrize(
    "change",
    [
        {"enabled": False},
        {"deleted_at": datetime(2026, 11, 12, tzinfo=UTC)},
        # Moved to another Base URL since this member's process started.
        {"credential_revision": 4},
    ],
)
async def test_a_connection_changed_under_a_running_process_stops_serving_it(change) -> None:
    store, provider = _Store(), _Provider()
    assert store.connection is not None
    store.connection.update(change)
    response = await _send(store, provider)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "provided_unavailable"
    assert provider.requests == [] and store.charges == []


async def test_a_residency_never_bound_to_the_connection_is_refused() -> None:
    store, provider = _Store(), _Provider()
    store.bound_revision = None
    response = await _send(store, provider)
    assert response.status_code == 403 and provider.requests == []


# -- what may be asked -----------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/responses/resp_1"),
        ("POST", "/v1/embeddings"),
        # Dot segments are refused too; ``test_operator_models_domain`` has
        # them, since this client normalizes one away before sending it.
        ("POST", "/v1/chat/completions/"),
        ("POST", "/v1/chat%2Fcompletions"),
        ("POST", "/v2/chat/completions"),
        ("GET", "/healthz"),
        ("GET", "/v1/agent/jobs"),
    ],
)
async def test_only_the_listed_paths_are_forwarded(method, path) -> None:
    store, provider = _Store(), _Provider()
    response = await _send(store, provider, method, path)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"
    assert provider.requests == [] and store.charges == []


async def test_the_destination_is_the_connections_whatever_the_request_says() -> None:
    store, provider = _Store(), _Provider()
    await _send(store, provider, headers={"Host": "evil.example", "X-Forwarded-Host": "evil"})
    assert provider.requests[0].url.host == "provider.example"
    assert provider.requests[0].headers["host"] == "provider.example"


@pytest.mark.parametrize("model", ["model-9", "MODEL-1"])
async def test_a_model_the_connection_does_not_list_is_refused(model) -> None:
    store, provider = _Store(), _Provider()
    response = await _send(store, provider, json={"model": model, "messages": []})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"
    assert provider.requests == [] and store.charges == []


@pytest.mark.parametrize(
    "body",
    [b"not json", b"[]", b'{"messages": []}', b'{"model": 7}', b"[" * 50_000 + b"]" * 50_000],
    ids=["not-json", "a-list", "no-model", "model-not-text", "nested-too-deep"],
)
async def test_an_inference_request_must_name_its_model(body) -> None:
    store, provider = _Store(), _Provider()
    response = await _send(
        store, provider, content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"
    assert provider.requests == []


async def test_a_body_too_large_is_refused_before_it_is_read(monkeypatch) -> None:
    monkeypatch.setattr(model_relay, "MAX_BODY_BYTES", 64)
    store, provider = _Store(), _Provider()
    response = await _send(store, provider, content=b"x" * 65)
    assert response.status_code == 413 and provider.requests == []


# -- who it is offered to, and budgets --------------------------------------------


@pytest.mark.parametrize(
    "store", [{"offered": False}, {"prices": None}], ids=["withheld", "no-prices"]
)
async def test_a_model_not_offered_to_the_owner_is_refused(store) -> None:
    store, provider = _Store(**store), _Provider()
    response = await _send(store, provider)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"
    assert provider.requests == [] and store.charges == []


async def test_a_spent_budget_is_a_403_a_client_does_not_retry() -> None:
    store, provider = _Store(budget=2_000_000, spent=2_000_000), _Provider()
    response = await _send(store, provider)
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "allowance_exhausted" and "00:00 UTC" in error["message"]
    assert "retry-after" not in response.headers
    assert provider.requests == [] and store.charges == []


async def test_the_request_that_uses_the_budget_up_is_served_and_the_next_is_not() -> None:
    # One answer costs exactly what is left.
    store, provider = _Store(budget=_COST), _Provider()
    assert (await _send(store, provider)).status_code == 200
    assert (await _send(store, provider)).status_code == 403
    assert len(provider.requests) == 1 and store.spent == _COST


async def test_a_refusal_is_shaped_for_the_api_it_was_sent_to() -> None:
    provider = _Provider()
    anthropic = await _send(_Store(budget=1, spent=1), provider, path="/v1/messages")
    assert anthropic.status_code == 403
    assert anthropic.json() == {
        "type": "error",
        "error": {
            "type": "permission_error",
            "message": anthropic.json()["error"]["message"],
            "code": "allowance_exhausted",
        },
    }
    openai = await _send(_Store(budget=1, spent=1), provider)
    assert set(openai.json()["error"]) == {"message", "type", "param", "code"}


# -- what a request is charged ---------------------------------------------------


def _event_stream(*events: dict | str) -> bytes:
    return "".join(
        f"data: {event if isinstance(event, str) else json.dumps(event)}\n\n" for event in events
    ).encode()


def _streaming(body: bytes, content_type: str = "text/event-stream"):
    async def handle(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(
            200, headers={"content-type": content_type}, stream=httpx.ByteStream(body)
        )

    return handle


async def test_a_streamed_chat_answer_is_charged_by_its_last_chunk() -> None:
    store = _Store()
    stream = _event_stream(
        {"choices": [{"delta": {"content": "Hi"}}]},
        {
            "choices": [],
            "usage": {
                "prompt_tokens": 100_000,
                "completion_tokens": 2_000,
                "total_tokens": 102_000,
                "prompt_tokens_details": {"cached_tokens": 60_000},
            },
        },
        "[DONE]",
    )
    async with _upstream(_streaming(stream)) as upstream:
        response = await _send(store, _Provider(), upstream=upstream)

    # The stream reaches the CLI as the provider sent it.
    assert response.content == stream
    # 40k tokens at $2, 60k cached at $0.50 and 2k out at $10, per million.
    assert store.charges == [_charge(130_000, prompt=100_000, cached=60_000, output=2_000)]


async def test_a_messages_answer_is_charged_with_its_cache_counts_added_in() -> None:
    store = _Store()
    stream = _event_stream(
        {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 500,
                    "cache_read_input_tokens": 9_000,
                    "cache_creation_input_tokens": 500,
                    "output_tokens": 1,
                }
            },
        },
        {"type": "message_delta", "delta": {}, "usage": {"output_tokens": 300}},
        {"type": "message_stop"},
    )
    async with _upstream(_streaming(stream)) as upstream:
        await _send(
            store,
            _Provider(),
            path="/v1/messages",
            bearer=None,
            headers={"x-api-key": _placeholder()},
            upstream=upstream,
        )

    # Written tokens have no price of their own here, so they are plain input:
    # 1,000 at $2, 9,000 read at $0.50, 300 out at $10.
    assert store.charges == [_charge(9_500, prompt=10_000, cached=9_000, written=500, output=300)]


async def test_a_responses_answer_is_charged_by_the_event_that_ends_it() -> None:
    store = _Store()
    usage = {"input_tokens": 4_000, "output_tokens": 600, "total_tokens": 4_600}
    stream = _event_stream(
        {"type": "response.output_text.delta", "delta": "Hi"},
        {"type": "response.completed", "response": {"id": "resp_1", "usage": usage}},
    )
    async with _upstream(_streaming(stream)) as upstream:
        await _send(store, _Provider(), path="/v1/responses", upstream=upstream)

    assert store.charges == [_charge(14_000, prompt=4_000, output=600)]


async def test_an_answer_that_names_no_usage_is_charged_by_the_size_of_its_request() -> None:
    store, provider = _Store(), _Provider(body={"id": "answer"})
    body = json.dumps({"model": "model-1", "messages": [{"content": "x" * 4000}]}).encode()
    response = await _send(
        store, provider, content=body, headers={"content-type": "application/json"}
    )
    assert response.status_code == 200
    assert store.charges == [_estimate(body)]
    assert store.charges[0]["input_tokens"] > 1000


async def test_a_screenshot_in_a_request_is_not_estimated_as_megabytes_of_text() -> None:
    store, provider = _Store(budget=50_000_000), _Provider(body={"id": "answer"})
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + "A" * 2_000_000}}
    body = {"model": "model-1", "messages": [{"role": "user", "content": [image]}]}
    response = await _send(store, provider, json=body)
    assert response.status_code == 200
    [charge] = store.charges
    # The gateway's flat amount for an image, not half a million tokens of text.
    assert IMAGE_TOKEN_ESTIMATE <= charge["input_tokens"] < IMAGE_TOKEN_ESTIMATE + 50
    assert charge["cost_microusd"] < 300


async def test_an_answer_the_provider_encoded_unasked_is_charged_by_estimate() -> None:
    store = _Store()
    encoded = gzip.compress(json.dumps(_ANSWER).encode())

    async def handle(request: httpx.Request) -> httpx.Response:
        await request.aread()
        return httpx.Response(
            200,
            headers={"content-type": "application/json", "content-encoding": "gzip"},
            stream=httpx.ByteStream(encoded),
        )

    async with _upstream(handle) as upstream:
        response = await _send(store, _Provider(), upstream=upstream)

    # Passed on as it came, for the CLI to decode.
    assert response.json() == _ANSWER
    [charge] = store.charges
    assert charge["estimated"] and charge["output_tokens"] == 0


@pytest.mark.parametrize("status", [302, 307, 400, 404, 422, 429, 500, 502, 503, 529])
async def test_an_answer_that_is_an_error_costs_nothing(status) -> None:
    store, provider = _Store(), _Provider(status, {"error": "busy", "usage": _USAGE})
    response = await _send(store, provider)
    # Relayed as it is: the CLI decides its own retry from it.
    assert response.status_code == status and response.json()["error"] == "busy"
    assert store.charges == []


async def test_an_unreachable_provider_is_a_502_and_costs_nothing() -> None:
    store, provider = _Store(), _Provider(fail=True)
    response = await _send(store, provider)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_unreachable"
    assert store.charges == []


@pytest.mark.parametrize(
    "error", [httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError]
)
async def test_a_provider_that_never_answers_is_a_502_and_costs_nothing(error) -> None:
    store, slots, reserved = _Store(), model_relay.Slots(), model_relay.Reserved()

    async def handle(request: httpx.Request) -> httpx.Response:
        raise error("no answer", request=request)

    upstream = _upstream(handle)
    try:
        response = await _send(
            store, _Provider(), slots=slots, reserved=reserved, upstream=upstream
        )
    finally:
        await upstream.aclose()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_unreachable"
    assert store.charges == []
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 0


async def test_a_failure_of_the_relays_own_before_sending_costs_nothing() -> None:
    """The owner does not pay for a request the provider never saw."""
    store, provider = _Store(), _Provider()
    slots, reserved = model_relay.Slots(), model_relay.Reserved()

    async def broken(_base_url: str) -> None:
        raise RuntimeError("the guard itself failed")

    with pytest.raises(RuntimeError, match="the guard itself failed"):
        await _send(store, provider, slots=slots, reserved=reserved, guard=broken)
    assert provider.requests == []
    assert store.charges == []
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 0


async def test_a_failure_after_the_provider_had_the_request_is_charged_by_estimate() -> None:
    """The provider may have served and billed it, and its answer was never read."""
    store, slots, reserved = _Store(), model_relay.Slots(), model_relay.Reserved()
    had: list[bytes] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        had.append(await request.aread())
        raise RuntimeError("lost after sending")

    upstream = _upstream(handle)
    try:
        with pytest.raises(RuntimeError, match="lost after sending"):
            await _send(store, _Provider(), slots=slots, reserved=reserved, upstream=upstream)
    finally:
        await upstream.aclose()
    assert store.charges == [_estimate(had[0])]
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 0


def _events(records: list[logging.LogRecord], event: str) -> list[logging.LogRecord]:
    return [record for record in records if record.getMessage() == event]


async def test_a_cost_that_could_not_be_written_is_written_later(caplog) -> None:
    """The answer is not failed and the cost is not let go: it is counted until it lands."""
    store, provider, reserved = _Store(), _Provider(), model_relay.Reserved()
    pending = model_relay.PendingCharges(reserved, delays=(3600.0,))
    store.write_failures = [OSError("the database is away")]
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        response = await _send(store, provider, reserved=reserved, pending=pending)
        assert response.status_code == 200 and response.json() == _ANSWER
        # Not written yet, and counted against the owner's budget meanwhile.
        assert store.charges == [] and pending.count == 1
        assert reserved.held(_BUDGET) == _COST
        [retry] = _events(caplog.records, "agent_model_relay_charge_retry")
        assert (retry.user_id, retry.model, retry.cost_microusd) == ("user_1", "model-1", _COST)

        # A process that stops makes one last try first.
        await pending.close()
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]
    assert store.days == [datetime.now(UTC).date()]
    assert reserved.held(_BUDGET) == 0 and pending.count == 0
    [late] = _events(caplog.records, "agent_model_relay_charged_late")
    assert late.cost_microusd == _COST


async def test_a_charge_waiting_to_be_written_holds_back_the_owners_next_request() -> None:
    store = _Store(budget=_COST)
    reserved = model_relay.Reserved()
    pending = model_relay.PendingCharges(reserved, delays=(3600.0,))
    store.write_failures = [OSError("the database is away")]
    first = await _send(store, _Provider(), reserved=reserved, pending=pending)
    assert first.status_code == 200 and store.spent == 0
    # What is left of the budget is spoken for until that charge is written.
    second = await _send(store, _Provider(), reserved=reserved, pending=pending)
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "budget_reserved"
    await pending.close()
    assert store.spent == _COST


async def test_a_charge_is_tried_again_until_it_lands(caplog) -> None:
    store, reserved = _Store(), model_relay.Reserved()
    pending = model_relay.PendingCharges(reserved, delays=(0.0, 0.0, 0.0))
    store.write_failures = [OSError("away"), ConnectionRefusedError("still away")]
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        await _send(store, _Provider(), reserved=reserved, pending=pending)
        for _ in range(50):
            if pending.count == 0:
                break
            await asyncio.sleep(0)
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]
    assert len(_events(caplog.records, "agent_model_relay_charge_retry")) == 2
    assert reserved.held(_BUDGET) == 0


async def test_a_charge_whose_tries_run_out_is_reported_with_its_amount(caplog) -> None:
    store, reserved = _Store(), model_relay.Reserved()
    pending = model_relay.PendingCharges(reserved, delays=(0.0, 0.0))
    store.write_failures = [OSError("away")] * 3
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        await _send(store, _Provider(), reserved=reserved, pending=pending)
        for _ in range(50):
            if pending.count == 0:
                break
            await asyncio.sleep(0)
    assert store.charges == []
    [lost] = _events(caplog.records, "agent_model_relay_charge_lost")
    assert (lost.levelno, lost.user_id, lost.cost_microusd) == (logging.ERROR, "user_1", _COST)
    # Given up, it is no longer held against the budget.
    assert reserved.held(_BUDGET) == 0


async def test_a_charge_whose_outcome_is_unknown_is_reported_not_written_twice(caplog) -> None:
    store, reserved = _Store(), model_relay.Reserved()
    pending = model_relay.PendingCharges(reserved, delays=(0.0,))
    store.write_failures = [ChargeOutcomeUnknown("connection lost after sending")]
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        response = await _send(store, _Provider(), reserved=reserved, pending=pending)
    assert response.status_code == 200
    assert pending.count == 0 and store.charges == [] and store.write_failures == []
    [unknown] = _events(caplog.records, "agent_model_relay_charge_unknown")
    assert (unknown.levelno, unknown.cost_microusd) == (logging.ERROR, _COST)
    assert reserved.held(_BUDGET) == 0


async def test_closing_with_nothing_waiting_returns_at_once() -> None:
    pending = model_relay.PendingCharges(model_relay.Reserved())
    await asyncio.wait_for(pending.close(), 1)
    assert pending.count == 0


async def test_a_base_url_that_stopped_resolving_publicly_is_never_dialled() -> None:
    async def blocked(_base_url: str) -> None:
        raise CheckStop("blocked_address")

    store, provider = _Store(), _Provider()
    response = await _send(store, provider, guard=blocked)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_unreachable"
    assert provider.requests == [] and store.charges == []


async def test_the_provider_refusing_the_deployments_key_is_not_the_clis_401() -> None:
    store, provider = _Store(), _Provider(401, {"error": {"message": f"bad key {_KEY}"}})
    response = await _send(store, provider)
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_refused_credential"
    # Nothing the provider said about the key is passed on.
    assert _KEY not in response.text
    assert store.key_refused == [True]
    assert store.charges == []


async def test_a_key_that_works_again_clears_the_warning() -> None:
    store, provider = _Store(), _Provider()
    assert store.connection is not None
    store.connection["key_refused_at"] = datetime(2026, 11, 12, tzinfo=UTC)
    assert (await _send(store, provider)).status_code == 200
    assert store.key_refused == [False]


# -- requests in flight ----------------------------------------------------------


async def test_an_owner_at_their_concurrency_cap_is_asked_to_retry() -> None:
    store, provider, slots = _Store(), _Provider(), model_relay.Slots()
    assert store.connection is not None
    store.connection["user_concurrency"] = 1
    user = ("user", _CONNECTION, "user_1")
    assert slots.take([(user, 1)])
    response = await _send(store, provider, slots=slots)
    assert response.status_code == 429
    assert response.headers["retry-after"] == "1"
    assert response.json()["error"]["code"] == "concurrency_limited"
    assert provider.requests == [] and store.charges == []
    # The refused request took nothing.
    assert slots.held(user) == 1


async def test_a_model_at_its_deployment_wide_cap_is_asked_to_retry() -> None:
    store, provider, slots = _Store(), _Provider(), model_relay.Slots()
    store.global_concurrency = 2
    model = ("model", _CONNECTION, "model-1")
    assert slots.take([(model, 2)]) and slots.take([(model, 2)])
    response = await _send(store, provider, slots=slots)
    assert response.status_code == 429 and provider.requests == []
    # Another model of the same connection is not held back by it.
    other = await _send(store, provider, slots=slots, json={"model": "model-2", "messages": []})
    assert other.status_code == 200


async def test_a_request_in_flight_holds_its_estimate_of_the_budget_until_it_ends() -> None:
    body = _Caller.BODY
    estimate = _estimate(body)["cost_microusd"]
    assert estimate > 0
    store, reserved = _Store(), model_relay.Reserved()
    reached, answer = asyncio.Event(), asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        reached.set()
        await answer.wait()
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=httpx.ByteStream(json.dumps(_ANSWER).encode()),
        )

    async with _upstream(slow) as upstream:
        caller = _Caller(_app(store, upstream, reserved=reserved))
        await asyncio.wait_for(reached.wait(), 5)
        assert reserved.held(_BUDGET) == estimate and store.charges == []

        answer.set()
        assert await caller.ended() is None
    # Its real cost has replaced what it held.
    assert reserved.held(_BUDGET) == 0
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]


async def test_a_request_holds_no_more_than_the_budget_has_left() -> None:
    body = _Caller.BODY
    estimate = _estimate(body)["cost_microusd"]
    store, reserved = _Store(budget=estimate + 5, spent=10), model_relay.Reserved()
    reached = asyncio.Event()

    async def silent(request: httpx.Request) -> httpx.Response:
        reached.set()
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async with _upstream(silent) as upstream:
        caller = _Caller(_app(store, upstream, reserved=reserved))
        await asyncio.wait_for(reached.wait(), 5)
        assert reserved.held(_BUDGET) == estimate - 5
        caller.left.set()
        await caller.ended()
    assert reserved.held(_BUDGET) == 0


async def test_a_budget_spoken_for_by_requests_in_flight_asks_the_next_to_retry() -> None:
    """Not used up, so not the 403 that ends a turn: what is in flight may cost less."""
    store, provider, reserved = _Store(budget=1000, spent=400), _Provider(), model_relay.Reserved()
    reserved.hold(_BUDGET, 600)
    slots = model_relay.Slots()

    response = await _send(store, provider, slots=slots, reserved=reserved)

    assert response.status_code == 429
    assert response.headers["retry-after"] == "2"
    assert response.json()["error"]["code"] == "budget_reserved"
    assert provider.requests == [] and store.charges == []
    # The refused request took nothing and held nothing.
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 600

    # Once what was in flight has ended, the same request is served.
    reserved.free(_BUDGET, 600)
    assert (await _send(store, provider, slots=slots, reserved=reserved)).status_code == 200


@pytest.mark.parametrize(
    "provider",
    [_Provider(), _Provider(500), _Provider(401), _Provider(fail=True)],
    ids=["served", "provider-error", "key-refused", "unreachable"],
)
async def test_every_ending_gives_its_slots_and_its_reservation_back(provider) -> None:
    store, slots, reserved = _Store(), model_relay.Slots(), model_relay.Reserved()
    await _send(store, provider, slots=slots, reserved=reserved)
    assert slots.held(("user", _CONNECTION, "user_1")) == 0
    assert slots.held(("model", _CONNECTION, "model-1")) == 0
    assert reserved.held(_BUDGET) == 0


async def test_a_caller_that_leaves_before_the_answer_ends_its_request() -> None:
    """A stopped turn's request is not waited on, at the provider or in the caps."""
    store, slots, reserved = _Store(), model_relay.Slots(), model_relay.Reserved()
    reached, abandoned = asyncio.Event(), asyncio.Event()

    async def silent(request: httpx.Request) -> httpx.Response:
        reached.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            abandoned.set()
            raise
        raise AssertionError("unreachable")

    async with _upstream(silent) as upstream:
        caller = _Caller(_app(store, upstream, slots=slots, reserved=reserved))
        await asyncio.wait_for(reached.wait(), 5)
        assert _slots_held(slots) == 2 and not caller.call.done()

        caller.left.set()
        assert await caller.ended() is None
    assert abandoned.is_set()
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 0
    # The provider had the request and may bill for it: charged by estimate.
    assert store.charges == [_estimate(_Caller.BODY)]


async def test_a_caller_that_stays_is_answered_however_long_the_provider_takes() -> None:
    store, slots = _Store(), model_relay.Slots()
    reached, answer = asyncio.Event(), asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        reached.set()
        await answer.wait()
        return httpx.Response(200, stream=httpx.ByteStream(b'{"id": "answer"}'))

    async with _upstream(slow) as upstream:
        caller = _Caller(_app(store, upstream, slots=slots))
        await asyncio.wait_for(reached.wait(), 5)
        answer.set()
        assert await caller.ended() is None
    assert caller.sent[0]["status"] == 200
    assert json.loads(b"".join(m.get("body", b"") for m in caller.sent[1:])) == {"id": "answer"}
    assert _slots_held(slots) == 0


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_caller_that_leaves_mid_answer_closes_the_providers_stream(spec_version) -> None:
    store, slots, reserved = _Store(), model_relay.Slots(), model_relay.Reserved()
    stream = _OpenStream(then=b"data: more\n\n")

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(
            _app(store, upstream, slots=slots, reserved=reserved), spec_version=spec_version
        )
        await asyncio.wait_for(caller.heard.wait(), 5)
        assert _slots_held(slots) == 2 and not stream.closed

        caller.left.set()
        if spec_version == "2.4":
            # Nothing tells this server; its next send is what fails.
            stream.go.set()
        await caller.ended()
    assert stream.closed
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 0
    # The answer never got as far as its usage: charged by estimate.
    assert store.charges == [_estimate(_Caller.BODY)]


async def test_a_chat_stream_cut_off_is_charged_the_output_it_had_produced() -> None:
    """As the gateway bills a stream its client closed: the prompt, and what was produced."""
    store, reserved = _Store(), model_relay.Reserved()
    produced = _event_stream({"choices": [{"delta": {"content": "x" * 400}}]})

    class _Begun(_OpenStream):
        async def __aiter__(self):
            yield produced
            await self.go.wait()

    stream = _Begun()

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(_app(store, upstream, reserved=reserved))
        await asyncio.wait_for(caller.heard.wait(), 5)
        caller.left.set()
        await caller.ended()
    assert stream.closed and reserved.held(_BUDGET) == 0
    # 400 bytes of answer at four a token.
    assert store.charges == [_estimate(_Caller.BODY, output=100)]


async def test_an_answer_cut_short_is_charged_what_it_had_named_by_then() -> None:
    """A Messages stream names its input on the first event; a Stop does not lose that."""
    store, reserved = _Store(), model_relay.Reserved()
    first = _event_stream(
        {
            "type": "message_start",
            "message": {"usage": {"input_tokens": 200, "cache_read_input_tokens": 8_000}},
        }
    )

    class _Begun(_OpenStream):
        async def __aiter__(self):
            yield first
            await self.go.wait()

    stream = _Begun()

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(_app(store, upstream, reserved=reserved), path="/v1/messages")
        await asyncio.wait_for(caller.heard.wait(), 5)
        caller.left.set()
        await caller.ended()
    assert stream.closed and reserved.held(_BUDGET) == 0
    # 200 plain at $2 and 8,000 read at $0.50; no output had been named yet.
    assert store.charges == [_charge(4_400, prompt=8_200, cached=8_000, output=0, estimated=True)]


async def test_an_answer_the_provider_breaks_off_is_closed_and_gives_its_slots_back() -> None:
    store, slots, reserved = _Store(), model_relay.Slots(), model_relay.Reserved()
    stream = _OpenStream(then=httpx.ReadError("connection reset"))

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(_app(store, upstream, slots=slots, reserved=reserved))
        await asyncio.wait_for(caller.heard.wait(), 5)
        stream.go.set()
        assert isinstance(await caller.ended(), httpx.ReadError)
    assert stream.closed
    assert _slots_held(slots) == 0 and reserved.held(_BUDGET) == 0
    assert store.charges == [_estimate(_Caller.BODY)]


# -- what is recorded ------------------------------------------------------------


async def test_a_request_is_logged_by_who_and_what_never_by_content(caplog) -> None:
    store, provider = _Store(), _Provider()
    secret_prompt = "the-owners-private-prompt"
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        await _send(
            store, provider, json={"model": "model-1", "messages": [{"content": secret_prompt}]}
        )
    [record] = [r for r in caplog.records if r.getMessage() == "agent_model_relay_request"]
    assert (record.job_id, record.user_id, record.model, record.status) == (
        "ajob_1",
        "user_1",
        "model-1",
        200,
    )
    [charged] = [r for r in caplog.records if r.getMessage() == "agent_model_relay_charged"]
    assert (charged.job_id, charged.user_id, charged.model) == ("ajob_1", "user_1", "model-1")
    assert (charged.cost_microusd, charged.input_tokens, charged.output_tokens) == (
        _COST,
        1000,
        100,
    )
    assert charged.estimated is False
    for logged_record in (record, charged):
        logged = json.dumps({k: str(v) for k, v in logged_record.__dict__.items()})
        for never in (secret_prompt, _KEY, _placeholder()):
            assert never not in logged


async def test_without_a_database_the_relay_refuses() -> None:
    provider = _Provider()
    response = await _send(None, provider)
    assert response.status_code == 503 and provider.requests == []
