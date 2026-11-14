"""The model relay: a sandbox's agent calls a provided model without holding its key.

A connection the deployment provides keeps its key in the control plane. A
member's CLI on one is told its provider is this relay (the connection's own
path on the host's model tunnel) and holds a residency's ``amd.`` placeholder
(:func:`cloud_agent.tokens.mint_residency_model_token`) as its API key, which
no provider accepts. On every request this relay resolves the placeholder to
the running turn it speaks for, checks the turn's owner has budget left today,
forwards the request to the connection's own endpoint with the deployment's
key, and charges the owner what the answer says it used.

**The placeholder is fenced like the other relays' credentials.** It buys
something only while its residency is the chat's hot one and is serving a
running, leased, unstopped turn on an enrolled host
(``OperatorModelRepository.resolve_relay_caller``), and only for the provided
connection that turn runs on, at the revision the member's process was started
on. Stop, a superseded attempt, a rotated residency, a removed host, and a
connection disabled, deleted or moved to another Base URL all take effect on
the next request, with nothing to revoke anywhere.

**It is not a proxy.** The destination is the connection's Base URL and
nothing a request says changes it. Only the inference and listing paths an
agent CLI calls under that URL are forwarded, matched whole
(:func:`cloud_agent.domain.operator_models.relay_route`), and only for a model
the connection lists. A credential that is not a placeholder is refused, not
passed on: an owner's own key never comes this way. Of a request's headers
only a short list goes on (``_FORWARDED_HEADERS``): a sandbox borrows the
deployment's key, not what a header can make that key do.

**A request is charged here, outside the sandbox, for what it used.** Each
owner has a budget per UTC day on a connection's models together. The answer
to an inference request names the tokens it used (``domain.model_usage``);
when it ends, those tokens at the model's prices are added to what the owner
spent today (``record_operator_request``), with the gateway's formula and
rounding. An answer that named no usage, or was cut short before it could, is
charged by estimate, as the gateway bills a stream its client closed: the
request's text as input, and the text the answer produced so far as output
(``domain.model_usage``). A request the provider did not serve (unreachable,
or answered with an error) costs nothing. A charge that could not be written
stays counted against the owner's budget and is written again later
(``_model_relay_charges``), as the gateway keeps a cost it has not written.

**What is in flight is held back.** A cost is known only when an answer ends,
so each admitted request reserves its estimate of the owner's budget until
then (:class:`Reserved`), and a request that finds the rest of the budget
reserved is asked to retry. Two caps on requests in flight, per owner and per
model, are kept the same way (:class:`Slots`). All of this is per process: a
deployment that runs several relay processes has each count its own, and an
owner can pass a budget by about what their requests in flight cost.

**Nothing of a request or an answer is stored or logged.** A request body is
read once to find the model it names and forwarded unchanged; an answer is
streamed back as it arrives, the provider's errors included, and read only
for its usage record. The provider is asked for no content encoding, so that
record can be read. The client every request is forwarded with keeps no
cookie from one answer to the next.

**A caller that leaves ends its request.** A stopped turn's CLI closes its
connection; from then on the provider is no longer waited for, and the
request's slots and reservation are free, whether or not an answer had begun.

See ``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from http.cookiejar import CookieJar, DefaultCookiePolicy
from typing import TYPE_CHECKING, Any, Protocol

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from cloud_agent.api._model_relay_charges import (
    Charge,
    PendingCharges,
    report,
    write_charge,
)
from cloud_agent.api.deps import get_storage
from cloud_agent.application.model_connection_check import guard_base_url
from cloud_agent.application.model_connection_stream import CheckStop
from cloud_agent.application.model_connections import operator_connection_cipher
from cloud_agent.crypto.credential_cipher import CredentialCipherError
from cloud_agent.crypto.domain_cipher import settings_root_key
from cloud_agent.domain.model_usage import TokenUsage, UsageReader, estimated_prompt_tokens
from cloud_agent.domain.operator_models import (
    end_user_tag,
    is_messages_path,
    relay_route,
    request_cost,
)
from cloud_agent.logging_utils import get_logger
from cloud_agent.mcp.transports import pinned_transport
from cloud_agent.ports.operator_models import ChargeOutcomeUnknown
from cloud_agent.tokens import InvalidAgentToken, parse_residency_model_token
from cloud_agent_shared.shared_protocol import is_residency_model_credential

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine

    from starlette.types import Receive, Scope, Send

    from cloud_agent.domain.operator_models import ModelPrices, RelayRoute
    from cloud_agent.ports import OperatorModelRepository

logger = get_logger("cloud_agent.model_relay")

router = APIRouter()

#: Never forwarded in either direction: they describe this hop, not the request.
_HOP_HEADERS = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailer",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
    }
)
#: The only headers of a client's request that go on to the provider, beside
#: the key, the owner tag and the content encoding the relay writes itself. A
#: provider reads some headers as instructions about the key: which
#: organization or project to bill, which application a call is attributed to,
#: where it claims to come from. A sandbox is lent the key's use, not that
#: authority, so a header is forwarded only when an agent CLI needs the
#: provider to see it.
_FORWARDED_HEADERS = frozenset(
    {
        "accept",
        "content-type",
        "user-agent",
        "anthropic-version",
        "anthropic-beta",
        "openai-beta",
    }
)
#: An answer can stream for minutes; a stalled connection still ends.
_UPSTREAM_TIMEOUT = httpx.Timeout(connect=15.0, read=600.0, write=600.0, pool=15.0)
#: The largest request body read and forwarded. A model request carries the
#: whole conversation, images included; a provider refuses far smaller ones.
MAX_BODY_BYTES = 32 * 1024 * 1024
#: Logged for a request whose caller left before the provider answered. Nobody
#: reads the response.
_CALLER_LEFT = 499
#: How long a written cost stays in :class:`Reserved` for the checks whose
#: read began before it landed: far longer than any read takes.
LANDED_WINDOW_SECONDS = 300.0
#: How long a request waits before retrying when the rest of its owner's
#: budget is reserved by requests still in flight.
_RESERVED_RETRY_AFTER = 2

#: The Anthropic error type a refusal of each status is answered as.
_ANTHROPIC_TYPES = {
    401: "authentication_error",
    403: "permission_error",
    404: "not_found_error",
    413: "request_too_large",
    429: "rate_limit_error",
    400: "invalid_request_error",
}


class _RelayStorage(Protocol):
    operator_models: OperatorModelRepository


class _Refused(Exception):
    """This request is not forwarded; ``code`` names why in the relay's vocabulary."""

    def __init__(self, status: int, code: str, message: str, *, retry_after: int = 0) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retry_after = retry_after


class Slots:
    """Requests in flight, per owner and per model, in this process.

    Not shared between relay processes: each one enforces the caps on its own
    requests. Asyncio-only, so taking and giving back need no lock.
    """

    def __init__(self) -> None:
        """Start with nothing in flight."""
        self._held: Counter[tuple[str, ...]] = Counter()

    def take(self, keys: list[tuple[tuple[str, ...], int | None]]) -> bool:
        """Take one slot under every key, or none when any is at its cap."""
        if any(cap is not None and self._held[key] >= cap for key, cap in keys):
            return False
        for key, _cap in keys:
            self._held[key] += 1
        return True

    def give(self, keys: list[tuple[tuple[str, ...], int | None]]) -> None:
        """Give back what :meth:`take` took."""
        for key, _cap in keys:
            self._held[key] -= 1
            if self._held[key] <= 0:
                del self._held[key]

    def held(self, key: tuple[str, ...]) -> int:
        """How many requests are in flight under ``key``."""
        return self._held[key]


class Reserved:
    """What requests in flight may yet cost, per owner and connection, in this process.

    A request's cost is known when its answer ends. Until then it holds its
    estimate of the owner's budget here, so requests running together cannot
    each be admitted against the same remainder. Like :class:`Slots`, not
    shared between relay processes.

    **A cost written lately** is kept here too, with when it landed
    (:meth:`landed`). A request reads what its owner spent from the database,
    then checks what is held here; a cost written in between, its hold given
    back, would be in neither. So a check also counts every cost that landed
    after its read began (:meth:`landed_since`), as the gateway's spend ledger
    counts a write for a while after it lands. One that landed before the read
    is in the read, and is not counted twice.
    """

    def __init__(self) -> None:
        """Start with nothing reserved."""
        self._held: Counter[tuple[str, ...]] = Counter()
        self._landed: deque[tuple[float, tuple[str, ...], int]] = deque()

    def landed(self, key: tuple[str, ...], amount: int) -> None:
        """Note that ``amount`` micro-dollars under ``key`` were just written."""
        now = time.monotonic()
        if amount > 0:
            self._landed.append((now, key, amount))
        self._forget(now)

    def landed_since(self, key: tuple[str, ...], since: float) -> int:
        """Micro-dollars under ``key`` written at or after ``since`` (``time.monotonic``)."""
        self._forget(time.monotonic())
        return sum(amount for when, held, amount in self._landed if held == key and when >= since)

    def _forget(self, now: float) -> None:
        while self._landed and now - self._landed[0][0] > LANDED_WINDOW_SECONDS:
            self._landed.popleft()

    def hold(self, key: tuple[str, ...], amount: int) -> None:
        """Reserve ``amount`` micro-dollars under ``key``."""
        if amount > 0:
            self._held[key] += amount

    def free(self, key: tuple[str, ...], amount: int) -> None:
        """Give back what :meth:`hold` reserved."""
        if amount <= 0:
            return
        self._held[key] -= amount
        if self._held[key] <= 0:
            del self._held[key]

    def held(self, key: tuple[str, ...]) -> int:
        """Micro-dollars reserved under ``key``."""
        return self._held[key]


#: This process's slots. A test replaces it through :func:`get_slots`.
_SLOTS = Slots()
#: This process's reservations. A test replaces it through :func:`get_reserved`.
_RESERVED = Reserved()
#: This process's charges not written yet, counted in its reservations. A test
#: replaces it through :func:`get_pending`.
_PENDING = PendingCharges(_RESERVED)


def get_slots() -> Slots:
    """Dependency: the process's in-flight counters."""
    return _SLOTS


def get_reserved() -> Reserved:
    """Dependency: the process's budget reservations."""
    return _RESERVED


def get_pending() -> PendingCharges:
    """Dependency: the process's charges not written yet."""
    return _PENDING


async def close_pending(timeout: float = 10.0) -> None:
    """Before the process stops: try each charge not written yet once more."""
    await _PENDING.close(timeout)


def get_relay_client(request: Request) -> httpx.AsyncClient | None:
    """Dependency: the listener's upstream client; ``None`` opens one for the request."""
    return getattr(request.app.state, "model_relay_http", None)


def get_endpoint_guard() -> Callable[[str], Awaitable[None]]:
    """Dependency: the check every upstream dial passes first.

    The control plane's own rule for an inference endpoint: public IPv4 only,
    and the dial that follows is pinned to the addresses checked.
    """
    return guard_base_url


def relay_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """The client the relay forwards with: pinned dials, no proxy, no redirects, no cookies.

    One client forwards every owner's requests, so it keeps nothing between
    them. Left to itself it would store a cookie an answer set and send it
    with the next request to that host, another owner's included.
    ``transport`` replaces the pinned dial in a test.
    """
    return httpx.AsyncClient(
        transport=transport if transport is not None else pinned_transport(),
        follow_redirects=False,
        trust_env=False,
        timeout=_UPSTREAM_TIMEOUT,
        cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=[])),
    )


@dataclass
class _Admitted:
    """One request this relay will forward: what it holds while in flight, and whom it charges."""

    store: _RelayStorage
    slots: Slots
    reserved: Reserved
    pending: PendingCharges
    caller: dict[str, Any]
    #: When the read of what the owner spent began (``time.monotonic``).
    read_at: float = 0.0
    #: The path's route; ``None`` until the caller's connection is known.
    route: RelayRoute | None = None
    model: str | None = None
    prices: ModelPrices | None = None
    keys: list[tuple[tuple[str, ...], int | None]] = field(default_factory=list)
    #: The request's text as input tokens: what is charged as its input
    #: when the answer names none.
    estimated_tokens: int = 0
    #: What is held of the owner's budget while the request is in flight.
    hold: tuple[tuple[str, ...], int] | None = None
    released: bool = False
    charged: bool = False
    #: Whether the request was handed to the provider, which may then have
    #: served it whatever happened here afterwards.
    sent: bool = False

    @property
    def connection(self) -> dict[str, Any]:
        """The provided connection the turn runs on."""
        connection: dict[str, Any] = self.caller["connection"]
        return connection

    def release(self) -> None:
        """Give the in-flight slots and the reserved budget back, once."""
        if self.released:
            return
        self.released = True
        self.slots.give(self.keys)
        if self.hold is not None:
            self.reserved.free(*self.hold)

    async def charge(self, used: TokenUsage, *, estimated: bool) -> None:
        """Add what the request cost to its owner's use today, once.

        ``used`` is what the answer named, or the estimate of it
        (``estimated``). A request that is not an inference call is never
        charged. A write that failed is kept and written later, counted
        against the owner's budget meanwhile; one whose outcome is unknown is
        reported, not written twice.
        """
        if self.charged or self.model is None or self.prices is None:
            return
        self.charged = True
        charge = Charge(
            connection_id=str(self.connection["id"]),
            user_id=str(self.caller["user_id"]),
            model=self.model,
            day=datetime.now(UTC).date(),
            cost_microusd=request_cost(self.prices, used),
            used=used,
            estimated=estimated,
            job_id=self.caller.get("job_id"),
        )
        try:
            await write_charge(self.store, charge)
        except ChargeOutcomeUnknown:
            report("agent_model_relay_charge_unknown", charge, error=True)
            return
        except Exception:
            # The answer was relayed: its cost is kept and written later
            # rather than failing the turn or being let go.
            logger.warning(
                "agent_model_relay_charge_retry",
                extra={"event": "agent_model_relay_charge_retry", **charge.facts()},
                exc_info=True,
            )
            self.pending.keep(self.store, charge)
            return
        # Before its hold is given back, so that the cost counts somewhere at
        # every moment for a check that read the database before it landed.
        self.reserved.landed(charge.budget_key, charge.cost_microusd)
        report("agent_model_relay_charged", charge)

    async def charge_estimate(self) -> None:
        """Charge a request whose answer was never read: its input, by estimate."""
        await self.charge(TokenUsage(prompt=self.estimated_tokens), estimated=True)


def _refusal(raw_path: str, refused: _Refused) -> Response:
    """A refusal in the error dialect of the API the path belongs to."""
    body: dict[str, Any]
    if is_messages_path(raw_path):
        body = {
            "type": "error",
            "error": {
                "type": _ANTHROPIC_TYPES.get(refused.status, "api_error"),
                "message": refused.message,
                "code": refused.code,
            },
        }
    else:
        body = {
            "error": {
                "message": refused.message,
                "type": "cloud_agent_model_relay",
                "param": None,
                "code": refused.code,
            }
        }
    headers = {"Retry-After": str(refused.retry_after)} if refused.retry_after else {}
    return JSONResponse(body, status_code=refused.status, headers=headers)


def _presented(request: Request) -> tuple[str, str] | None:
    """The credential a request carries and how: ``("bearer" | "x-api-key", value)``."""
    key = (request.headers.get("x-api-key") or "").strip()
    if key:
        return "x-api-key", key
    scheme, _, value = (request.headers.get("authorization") or "").partition(" ")
    if scheme.lower() == "bearer" and value.strip():
        return "bearer", value.strip()
    return None


async def _bounded_body(request: Request) -> bytes:
    """The request body, refused past :data:`MAX_BODY_BYTES` before it is all read."""
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _Refused(413, "request_too_large", "The request body is too large to relay.")
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_BODY_BYTES:
            raise _Refused(413, "request_too_large", "The request body is too large to relay.")
        chunks.append(chunk)
    return b"".join(chunks)


def _decoded(body: bytes) -> Any:
    """A request body as JSON; ``None`` when it is not, or is nested too deep to read."""
    try:
        return json.loads(body)
    except (ValueError, RecursionError):
        return None


def _named_model(document: Any) -> str | None:
    """The top-level ``model`` of a decoded request body; ``None`` when it names none."""
    model = document.get("model") if isinstance(document, dict) else None
    return model if isinstance(model, str) and model else None


def _live_connection(caller: dict[str, Any]) -> dict[str, Any]:
    """The provided connection this residency's process may still use, or refuse."""
    connection = caller.get("connection")
    current = (
        connection is not None
        and connection.get("deleted_at") is None
        and connection.get("enabled")
        and caller.get("bound_revision") == connection.get("credential_revision")
    )
    if not current:
        raise _Refused(
            403,
            "provided_unavailable",
            "This deployment no longer provides the model source this task runs on.",
        )
    assert connection is not None
    return connection


def _admit_inference(admitted: _Admitted, model: str | None, document: Any) -> None:
    """Check the model and the owner's budget, take the in-flight slots and reserve, or refuse."""
    connection, caller = admitted.connection, admitted.caller
    if model is None:
        raise _Refused(400, "invalid_request", "The request body must be JSON naming a model.")
    allowance, prices = caller.get("allowance"), caller.get("prices")
    if (
        model not in connection["models"]
        or allowance is None
        or not allowance.offered
        or prices is None
    ):
        raise _Refused(
            403, "model_not_allowed", "This deployment does not provide that model to this task."
        )
    if allowance.exhausted:
        raise _Refused(
            403,
            "allowance_exhausted",
            "Today's budget for the models this deployment provides is used up. "
            "It starts over at 00:00 UTC.",
        )
    connection_id, user_id = str(connection["id"]), str(caller["user_id"])
    keys: list[tuple[tuple[str, ...], int | None]] = [
        (("user", connection_id, user_id), int(connection["user_concurrency"])),
        (("model", connection_id, model), caller.get("global_concurrency")),
    ]
    if not admitted.slots.take(keys):
        raise _Refused(
            429,
            "concurrency_limited",
            "Too many requests to this model are in progress. Retry shortly.",
            retry_after=1,
        )
    admitted.keys, admitted.model, admitted.prices = keys, model, prices
    budget_key = ("budget", connection_id, user_id)
    room = (
        allowance.remaining_microusd
        - admitted.reserved.held(budget_key)
        - admitted.reserved.landed_since(budget_key, admitted.read_at)
    )
    if room <= 0:
        # Not used up: what is left is spoken for by requests that have not
        # ended. Their real cost is usually less, so this one may fit then.
        admitted.release()
        raise _Refused(
            429,
            "budget_reserved",
            "Requests in progress may use the rest of today's budget. Retry shortly.",
            retry_after=_RESERVED_RETRY_AFTER,
        )
    admitted.estimated_tokens = estimated_prompt_tokens(document)
    estimate = request_cost(prices, TokenUsage(prompt=admitted.estimated_tokens))
    admitted.hold = (budget_key, min(estimate, room))
    admitted.reserved.hold(*admitted.hold)


def _upstream_headers(
    request: Request, *, style: str, api_key: str, connection: dict[str, Any], user_id: str
) -> dict[str, str]:
    """The headers the provider is sent: the client's few that matter, and the deployment's key."""
    tag_header = (connection.get("end_user_header") or "").lower()
    headers = {
        name: value
        for name, value in request.headers.items()
        if name.lower() in _FORWARDED_HEADERS and name.lower() != tag_header
    }
    # The answer is streamed back as the provider sent it and read for its
    # usage record on the way, so the provider is asked not to encode it.
    headers["accept-encoding"] = "identity"
    if style == "x-api-key":
        headers["x-api-key"] = api_key
    else:
        headers["authorization"] = f"Bearer {api_key}"
    if tag_header:
        headers[tag_header] = end_user_tag(settings_root_key(), user_id)
    return headers


def _downstream_headers(response: httpx.Response) -> dict[str, str]:
    return {
        name: value
        for name, value in response.headers.items()
        if name.lower() not in _HOP_HEADERS or name.lower() == "content-length"
    }


@router.api_route("/{path:path}", methods=["GET", "POST"], include_in_schema=False)
async def relay(
    path: str,
    request: Request,
    store: _RelayStorage | None = Depends(get_storage),
    http: httpx.AsyncClient | None = Depends(get_relay_client),
    slots: Slots = Depends(get_slots),
    reserved: Reserved = Depends(get_reserved),
    pending: PendingCharges = Depends(get_pending),
    guard: Callable[[str], Awaitable[None]] = Depends(get_endpoint_guard),
) -> Response:
    """Forward one model request with the deployment's key, or refuse it."""
    del path  # The route is decided from the path exactly as it was sent.
    raw_path = (request.scope.get("raw_path") or b"/").split(b"?", 1)[0].decode("latin-1")
    started = time.monotonic()
    admitted: _Admitted | None = None
    try:
        admitted, style, body = await _admit(request, store, slots, reserved, pending, raw_path)
        response = await _forward(request, http, guard, admitted, style, body, raw_path)
    except _Refused as refused:
        if admitted is not None:
            admitted.release()
        _log(admitted, request, raw_path, refused.status, started, refused.code)
        return _refusal(raw_path, refused)
    except BaseException:
        if admitted is not None:
            try:
                if admitted.sent:
                    # The provider had the request and may have served it,
                    # whatever failed here afterwards: it is charged by
                    # estimate. One that never left costs its owner nothing.
                    await admitted.charge_estimate()
            finally:
                admitted.release()
        raise
    _log(admitted, request, raw_path, response.status_code, started, None)
    return response


async def _admit(
    request: Request,
    store: _RelayStorage | None,
    slots: Slots,
    reserved: Reserved,
    pending: PendingCharges,
    raw_path: str,
) -> tuple[_Admitted, str, bytes]:
    """Resolve the placeholder to its turn and settle whether this request may go on."""
    presented = _presented(request)
    if presented is None or not is_residency_model_credential(presented[1]):
        raise _Refused(
            401, "invalid_credential", "This relay accepts only a task's own credential."
        )
    try:
        residency = parse_residency_model_token(presented[1])
    except InvalidAgentToken:
        raise _Refused(
            401, "invalid_credential", "This credential is not one this relay issued."
        ) from None
    if store is None:
        raise _Refused(503, "relay_unavailable", "The model relay has no database configured.")
    body = await _bounded_body(request) if request.method == "POST" else b""
    document = _decoded(body) if body else None
    model = _named_model(document)
    # What the owner spent is read below; a cost that lands after this
    # moment may be missing from it (``Reserved.landed_since``).
    read_at = time.monotonic()
    caller = await store.operator_models.resolve_model_relay_caller(
        thread_id=residency["thread_id"],
        residency_generation=residency["residency_generation"],
        model=model,
    )
    if caller is None:
        raise _Refused(
            401, "invalid_credential", "This chat has no running turn, so it cannot reach a model."
        )
    admitted = _Admitted(
        store=store,
        slots=slots,
        reserved=reserved,
        pending=pending,
        caller=caller,
        read_at=read_at,
    )
    connection = _live_connection(caller)
    route = relay_route(str(connection["base_url"]), request.method, raw_path)
    if route is None:
        raise _Refused(404, "not_found", "This relay does not forward that path.")
    admitted.route = route
    if route.inference:
        _admit_inference(admitted, model, document)
    return admitted, presented[0], body


async def _send(
    request: Request,
    client: httpx.AsyncClient,
    guard: Callable[[str], Awaitable[None]],
    admitted: _Admitted,
    style: str,
    body: bytes,
    raw_path: str,
) -> httpx.Response | None:
    """Hand the request to the connection's endpoint: its answer, or ``None`` once the caller left."""
    connection = admitted.connection
    base_url = str(connection["base_url"])
    try:
        api_key = operator_connection_cipher().decrypt(str(connection["api_key_ciphertext"]))
    except CredentialCipherError:
        raise _Refused(
            503, "relay_unavailable", "This deployment cannot read its provider key."
        ) from None
    unreachable = _Refused(
        502, "provider_unreachable", "The model provider could not be reached from the relay."
    )
    try:
        await guard(base_url)
    except CheckStop:
        raise unreachable from None
    origin = httpx.URL(base_url).copy_with(raw_path=b"/")
    query = request.url.query
    target = raw_path + (f"?{query}" if query else "")
    try:
        upstream = client.build_request(
            request.method,
            origin.copy_with(raw_path=target.encode("latin-1")),
            headers=_upstream_headers(
                request,
                style=style,
                api_key=api_key,
                connection=connection,
                user_id=str(admitted.caller["user_id"]),
            ),
            content=body if request.method == "POST" else None,
            timeout=_UPSTREAM_TIMEOUT,
        )
        admitted.sent = True
        return await _unless_left(
            request, client.send(upstream, stream=True, follow_redirects=False)
        )
    except httpx.HTTPError:
        raise unreachable from None


async def _forward(
    request: Request,
    http: httpx.AsyncClient | None,
    guard: Callable[[str], Awaitable[None]],
    admitted: _Admitted,
    style: str,
    body: bytes,
    raw_path: str,
) -> Response:
    """Send the request to the connection's endpoint and stream its answer back."""
    client = http if http is not None else relay_client()

    async def close_client() -> None:
        if http is None:
            await client.aclose()

    try:
        answer = await _send(request, client, guard, admitted, style, body, raw_path)
    except BaseException:
        await close_client()
        raise
    if answer is None:
        # The provider had the request and may bill for it: charged by estimate.
        try:
            await admitted.charge_estimate()
        finally:
            admitted.release()
            await close_client()
        return Response(status_code=_CALLER_LEFT)

    reader = _usage_reader(admitted, answer)

    async def close() -> None:
        try:
            await answer.aclose()
        finally:
            try:
                # Charged before the reservation is given back, so that the
                # cost counts somewhere at every moment.
                if reader is not None:
                    used, estimated = reader.billed(admitted.estimated_tokens)
                    await admitted.charge(used, estimated=estimated)
            finally:
                admitted.release()
                await close_client()

    try:
        await _settle(admitted, answer.status_code)
    except BaseException:
        # The answer is not relayed, so its usage is never read. A refusal
        # costs nothing; after a failure the caller charges by estimate.
        reader = None
        await close()
        raise
    return _Relayed(answer, _metered(answer, reader), on_end=close)


def _usage_reader(admitted: _Admitted, answer: httpx.Response) -> UsageReader | None:
    """What reads the usage out of ``answer``; ``None`` for an answer that is not charged.

    Only an inference request the provider served is charged: its error or
    its redirect, like a listing or a token count, costs the owner nothing.
    """
    route = admitted.route
    if route is None or not route.inference or not 200 <= answer.status_code < 300:
        return None
    return UsageReader(
        route.api,
        content_type=answer.headers.get("content-type", ""),
        content_encoding=answer.headers.get("content-encoding", ""),
    )


async def _metered(answer: httpx.Response, reader: UsageReader | None) -> AsyncIterator[bytes]:
    """The answer's bytes as they arrive, shown to ``reader`` on the way."""
    async for chunk in answer.aiter_raw():
        if reader is not None:
            reader.feed(chunk)
        yield chunk


async def _left(request: Request) -> None:
    """Return once the caller's connection has closed."""
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _unless_left(
    request: Request, sending: Coroutine[Any, Any, httpx.Response]
) -> httpx.Response | None:
    """The provider's answer to ``sending``, or ``None`` once the caller has left.

    Until there is a response to stream, nothing else watches the caller's
    connection. A provider can take minutes to begin an answer, and a turn
    stopped meanwhile would go on holding its owner's and its model's slots
    until the provider answered or the read timed out. Cancelling the send
    closes the provider's connection, which is all a provider is ever told.
    """
    answer = asyncio.ensure_future(sending)
    left = asyncio.ensure_future(_left(request))
    try:
        await asyncio.wait({answer, left}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        # Reached when this request is itself cancelled, too.
        left.cancel()
        if not answer.done():
            answer.cancel()
        await asyncio.wait({answer, left})
    return None if answer.cancelled() else answer.result()


class _Relayed(StreamingResponse):
    """A provider's answer streamed back, and closed however that ends.

    Starlette stops pulling a body when its caller leaves, a send fails or
    its task is cancelled, and runs a background task only when nothing was
    raised. The provider's connection and the request's slots are given back
    here in every case.
    """

    def __init__(
        self,
        answer: httpx.Response,
        body: AsyncIterator[bytes],
        *,
        on_end: Callable[[], Awaitable[None]],
    ) -> None:
        """Stream ``body``, the bytes of ``answer`` as received; ``on_end`` runs once it is over."""
        super().__init__(
            body,
            status_code=answer.status_code,
            headers=_downstream_headers(answer),
        )
        self._on_end = on_end

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Send the response, then close what it held."""
        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._on_end()


async def _settle(admitted: _Admitted, status: int) -> None:
    """Act on the provider's status before its answer is relayed.

    A 401 is the provider refusing the deployment's key, which the member's
    CLI must not read as its own credential being wrong: it is answered as a
    502 and remembered for the administrator.
    """
    connection = admitted.connection
    connection_id = str(connection["id"])
    if status == 401:
        await admitted.store.operator_models.mark_operator_key_refused(
            connection_id=connection_id, refused=True
        )
        logger.error(
            "agent_model_relay_key_refused",
            extra={"event": "agent_model_relay_key_refused", "connection_id": connection_id},
        )
        raise _Refused(
            502,
            "provider_refused_credential",
            "The model provider refused this deployment's key. Its administrator has to fix it.",
        )
    if status < 400 and connection.get("key_refused_at") is not None:
        await admitted.store.operator_models.mark_operator_key_refused(
            connection_id=connection_id, refused=False
        )


def _log(
    admitted: _Admitted | None,
    request: Request,
    raw_path: str,
    status: int,
    started: float,
    refusal: str | None,
) -> None:
    """One structured line per request: who, which model, what happened. Never content."""
    caller = admitted.caller if admitted is not None else {}
    logger.info(
        "agent_model_relay_request",
        extra={
            "event": "agent_model_relay_request",
            "job_id": caller.get("job_id"),
            "thread_id": caller.get("thread_id"),
            "user_id": caller.get("user_id"),
            "connection_id": caller.get("model_connection_id"),
            "model": admitted.model if admitted is not None else None,
            "method": request.method,
            "route": raw_path.rsplit("/", 1)[-1][:32],
            "status": status,
            "refusal": refusal,
            "duration_ms": int((time.monotonic() - started) * 1000),
        },
    )


__all__ = [
    "MAX_BODY_BYTES",
    "PendingCharges",
    "Reserved",
    "Slots",
    "close_pending",
    "get_endpoint_guard",
    "get_pending",
    "get_relay_client",
    "get_reserved",
    "get_slots",
    "relay_client",
    "router",
]
