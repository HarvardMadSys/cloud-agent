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
from cloud_agent.domain.operator_models import ModelPrices
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


@pytest.fixture(autouse=True)
def _secrets(monkeypatch):
    monkeypatch.setenv("AGENT_CONTROL_TOKEN_SECRET", "model-relay-test-secret")
    monkeypatch.setenv("AGENT_SETTINGS_ENCRYPTION_KEY", _ROOT)


class _Store:
    """A running turn of ``athr_1`` (residency 2) by ``user_1`` on one provided connection."""

    def __init__(self, *, running: bool = True, prices: ModelPrices | None = _PRICES) -> None:
        self.running, self.prices = running, prices
        self.charges: list[dict[str, Any]] = []
        #: The day each charge was written for.
        self.days: list[Any] = []
        #: What the next writes raise, one each, before they succeed.
        self.write_failures: list[BaseException] = []
        self.key_refused: list[bool] = []
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
            "key_refused_at": None,
        }
        self.operator_models = self

    async def resolve_model_relay_caller(self, *, thread_id, residency_generation, model):
        if not self.running or (thread_id, residency_generation) != ("athr_1", 2):
            return None
        connection = None if self.on_other_connection else self.connection
        listed = connection is not None and model in connection["models"]
        return {
            "job_id": "ajob_1",
            "attempt_id": 1,
            "thread_id": thread_id,
            "user_id": "user_1",
            "model_connection_id": _CONNECTION if connection else "mconn_" + "0" * 32,
            "bound_revision": self.bound_revision if connection else None,
            "connection": connection,
            "prices": self.prices if listed else None,
        }

    async def record_operator_request(self, *, connection_id, user_id, day, **charge):
        assert (connection_id, user_id) == (_CONNECTION, "user_1")
        if self.write_failures:
            raise self.write_failures.pop(0)
        self.days.append(day)
        self.charges.append(charge)

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
    pending: model_relay.PendingCharges | None = None,
    guard: Any = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(model_relay.router)
    app.dependency_overrides[get_storage] = lambda: store
    app.dependency_overrides[model_relay.get_relay_client] = lambda: upstream
    later = pending or model_relay.PendingCharges(delays=(0.0,))
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
    pending: model_relay.PendingCharges | None = None,
    guard: Any = None,
    upstream: httpx.AsyncClient | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """One request to the relay; ``upstream`` is a client several requests share."""
    client = upstream or _upstream(provider.handle)
    app = _app(store, client, pending=pending, guard=guard)
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
    # Served for any model, and never recorded.
    store, provider = _Store(), _Provider()
    listed = await _send(store, provider, "GET", "/v1/models")
    counted = await _send(store, provider, path="/v1/messages/count_tokens")
    assert (listed.status_code, counted.status_code) == (200, 200)
    assert store.charges == []
    assert [str(request.url) for request in provider.requests] == [
        "https://provider.example/v1/models",
        "https://provider.example/v1/messages/count_tokens",
    ]


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


# -- what a model costs -----------------------------------------------------------


async def test_a_model_listed_without_prices_is_served_and_recorded_by_its_tokens() -> None:
    store, provider = _Store(prices=None), _Provider()
    response = await _send(store, provider)
    assert response.status_code == 200 and response.json() == _ANSWER
    assert store.charges == [_charge(0, prompt=1000, output=100)]


async def test_nothing_limits_how_much_one_owner_sends() -> None:
    store, provider = _Store(), _Provider()
    for _ in range(5):
        assert (await _send(store, provider)).status_code == 200
    assert len(provider.requests) == 5
    assert store.charges == [_charge(_COST, prompt=1000, output=100)] * 5


async def test_a_refusal_is_shaped_for_the_api_it_was_sent_to() -> None:
    provider = _Provider()
    body = {"model": "model-9", "messages": []}
    anthropic = await _send(_Store(), provider, path="/v1/messages", json=body)
    assert anthropic.status_code == 403
    assert anthropic.json() == {
        "type": "error",
        "error": {
            "type": "permission_error",
            "message": anthropic.json()["error"]["message"],
            "code": "model_not_allowed",
        },
    }
    assert "retry-after" not in anthropic.headers
    openai = await _send(_Store(), provider, json=body)
    assert set(openai.json()["error"]) == {"message", "type", "param", "code"}
    assert provider.requests == []


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
    store, provider = _Store(), _Provider(body={"id": "answer"})
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
    store = _Store()

    async def handle(request: httpx.Request) -> httpx.Response:
        raise error("no answer", request=request)

    upstream = _upstream(handle)
    try:
        response = await _send(store, _Provider(), upstream=upstream)
    finally:
        await upstream.aclose()
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_unreachable"
    assert store.charges == []


async def test_a_failure_of_the_relays_own_before_sending_costs_nothing() -> None:
    """The owner does not pay for a request the provider never saw."""
    store, provider = _Store(), _Provider()

    async def broken(_base_url: str) -> None:
        raise RuntimeError("the guard itself failed")

    with pytest.raises(RuntimeError, match="the guard itself failed"):
        await _send(store, provider, guard=broken)
    assert provider.requests == []
    assert store.charges == []


async def test_a_failure_after_the_provider_had_the_request_is_charged_by_estimate() -> None:
    """The provider may have served and billed it, and its answer was never read."""
    store = _Store()
    had: list[bytes] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        had.append(await request.aread())
        raise RuntimeError("lost after sending")

    upstream = _upstream(handle)
    try:
        with pytest.raises(RuntimeError, match="lost after sending"):
            await _send(store, _Provider(), upstream=upstream)
    finally:
        await upstream.aclose()
    assert store.charges == [_estimate(had[0])]


def _events(records: list[logging.LogRecord], event: str) -> list[logging.LogRecord]:
    return [record for record in records if record.getMessage() == event]


async def test_a_cost_that_could_not_be_written_is_written_later(caplog) -> None:
    """The answer is not failed and the record is not let go: it is written once it can be."""
    store, provider = _Store(), _Provider()
    pending = model_relay.PendingCharges(delays=(3600.0,))
    store.write_failures = [OSError("the database is away")]
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        response = await _send(store, provider, pending=pending)
        assert response.status_code == 200 and response.json() == _ANSWER
        # Not written yet, and kept to be written later.
        assert store.charges == [] and pending.count == 1
        [retry] = _events(caplog.records, "agent_model_relay_charge_retry")
        assert (retry.user_id, retry.model, retry.cost_microusd) == ("user_1", "model-1", _COST)

        # A process that stops makes one last try first.
        await pending.close()
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]
    assert store.days == [datetime.now(UTC).date()]
    assert pending.count == 0
    [late] = _events(caplog.records, "agent_model_relay_charged_late")
    assert late.cost_microusd == _COST


async def test_a_charge_is_tried_again_until_it_lands(caplog) -> None:
    store = _Store()
    pending = model_relay.PendingCharges(delays=(0.0, 0.0, 0.0))
    store.write_failures = [OSError("away"), ConnectionRefusedError("still away")]
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        await _send(store, _Provider(), pending=pending)
        for _ in range(50):
            if pending.count == 0:
                break
            await asyncio.sleep(0)
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]
    assert len(_events(caplog.records, "agent_model_relay_charge_retry")) == 2


async def test_a_charge_is_tried_until_it_lands_however_many_tries_that_takes(caplog) -> None:
    store = _Store()
    pending = model_relay.PendingCharges(delays=(0.0,))
    store.write_failures = [OSError("away")] * 12
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        await _send(store, _Provider(), pending=pending)
        for _ in range(200):
            if pending.count == 0:
                break
            await asyncio.sleep(0)
    assert store.charges == [_charge(_COST, prompt=1000, output=100)]
    assert len(_events(caplog.records, "agent_model_relay_charge_retry")) == 12
    assert _events(caplog.records, "agent_model_relay_charge_lost") == []


async def test_a_charge_still_unwritten_when_the_process_stops_is_reported(caplog) -> None:
    store = _Store()
    pending = model_relay.PendingCharges(delays=(0.0,))
    store.write_failures = [OSError("away")] * 10_000
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        await _send(store, _Provider(), pending=pending)
        for _ in range(20):
            await asyncio.sleep(0)
        # Still trying.
        assert pending.count == 1
        await pending.close()
    assert store.charges == []
    [lost] = _events(caplog.records, "agent_model_relay_charge_lost")
    assert (lost.levelno, lost.user_id, lost.cost_microusd) == (logging.ERROR, "user_1", _COST)
    assert pending.count == 0


async def test_a_charge_whose_outcome_is_unknown_is_reported_not_written_twice(caplog) -> None:
    store = _Store()
    pending = model_relay.PendingCharges(delays=(0.0,))
    store.write_failures = [ChargeOutcomeUnknown("connection lost after sending")]
    with caplog.at_level(logging.INFO, logger="cloud_agent.model_relay"):
        response = await _send(store, _Provider(), pending=pending)
    assert response.status_code == 200
    assert pending.count == 0 and store.charges == [] and store.write_failures == []
    [unknown] = _events(caplog.records, "agent_model_relay_charge_unknown")
    assert (unknown.levelno, unknown.cost_microusd) == (logging.ERROR, _COST)


async def test_closing_with_nothing_waiting_returns_at_once() -> None:
    pending = model_relay.PendingCharges()
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


async def test_a_caller_that_leaves_before_the_answer_ends_its_request() -> None:
    """A stopped turn's request is not waited on at the provider."""
    store = _Store()
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
        caller = _Caller(_app(store, upstream))
        await asyncio.wait_for(reached.wait(), 5)
        assert not caller.call.done()

        caller.left.set()
        assert await caller.ended() is None
    assert abandoned.is_set()
    # The provider had the request and may bill for it: charged by estimate.
    assert store.charges == [_estimate(_Caller.BODY)]


async def test_a_caller_that_stays_is_answered_however_long_the_provider_takes() -> None:
    store = _Store()
    reached, answer = asyncio.Event(), asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        reached.set()
        await answer.wait()
        return httpx.Response(200, stream=httpx.ByteStream(b'{"id": "answer"}'))

    async with _upstream(slow) as upstream:
        caller = _Caller(_app(store, upstream))
        await asyncio.wait_for(reached.wait(), 5)
        answer.set()
        assert await caller.ended() is None
    assert caller.sent[0]["status"] == 200
    assert json.loads(b"".join(m.get("body", b"") for m in caller.sent[1:])) == {"id": "answer"}


@pytest.mark.parametrize("spec_version", ["2.3", "2.4"])
async def test_a_caller_that_leaves_mid_answer_closes_the_providers_stream(spec_version) -> None:
    store = _Store()
    stream = _OpenStream(then=b"data: more\n\n")

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(_app(store, upstream), spec_version=spec_version)
        await asyncio.wait_for(caller.heard.wait(), 5)
        assert not stream.closed

        caller.left.set()
        if spec_version == "2.4":
            # Nothing tells this server; its next send is what fails.
            stream.go.set()
        await caller.ended()
    assert stream.closed
    # The answer never got as far as its usage: charged by estimate.
    assert store.charges == [_estimate(_Caller.BODY)]


async def test_a_chat_stream_cut_off_is_charged_the_output_it_had_produced() -> None:
    """As the gateway bills a stream its client closed: the prompt, and what was produced."""
    store = _Store()
    produced = _event_stream({"choices": [{"delta": {"content": "x" * 400}}]})

    class _Begun(_OpenStream):
        async def __aiter__(self):
            yield produced
            await self.go.wait()

    stream = _Begun()

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(_app(store, upstream))
        await asyncio.wait_for(caller.heard.wait(), 5)
        caller.left.set()
        await caller.ended()
    assert stream.closed
    # 400 bytes of answer at four a token.
    assert store.charges == [_estimate(_Caller.BODY, output=100)]


async def test_an_answer_cut_short_is_charged_what_it_had_named_by_then() -> None:
    """A Messages stream names its input on the first event; a Stop does not lose that."""
    store = _Store()
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
        caller = _Caller(_app(store, upstream), path="/v1/messages")
        await asyncio.wait_for(caller.heard.wait(), 5)
        caller.left.set()
        await caller.ended()
    assert stream.closed
    # 200 plain at $2 and 8,000 read at $0.50; no output had been named yet.
    assert store.charges == [_charge(4_400, prompt=8_200, cached=8_000, output=0, estimated=True)]


async def test_an_answer_the_provider_breaks_off_is_closed() -> None:
    store = _Store()
    stream = _OpenStream(then=httpx.ReadError("connection reset"))

    async def streaming(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=stream)

    async with _upstream(streaming) as upstream:
        caller = _Caller(_app(store, upstream))
        await asyncio.wait_for(caller.heard.wait(), 5)
        stream.go.set()
        assert isinstance(await caller.ended(), httpx.ReadError)
    assert stream.closed
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
