"""An owner signs in at the configured model provider and comes back with a key.

``start_sign_in`` issues a single-use state bound to the owner, with a PKCE
verifier only this service holds (encrypted in ``agent_oauth_states``, ten
minutes), and answers the provider's authorization page with the callback and
the S256 challenge. The provider signs the owner up or in, asks them to approve
a key, and sends the browser back to the web app's callback page with a
one-time code beside the state. ``finish_sign_in`` consumes that state for the
same owner, exchanges the code and the verifier at the provider's key URL for
an API key on the owner's provider account, verifies the configured models
with it and saves the ones an agent can use as a new connection of the owner's
own. From then on it is an ordinary connection: the key is write-only, and the
claim, residency and invalidation paths are the ones every connection takes.

Everything that can be refused without the key is checked before the code is
exchanged: the state, the feature switch, the owner's existing connections, the
models still free to list and the owner's hourly checks, which are reserved
first. A key the provider issues is therefore lost only when its own
verification or the save fails, and the refusal says so.

The key exchange is the only call to the provider's account side, made from
the control plane, to the address the egress policy checked, without
redirects, and bounded in time and size. Nothing here runs during a turn.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlencode

import httpx

from cloud_agent.application.model_connection_check import (
    CHECKS_PER_HOUR,
    CheckStop,
    guard_base_url,
)
from cloud_agent.application.model_connection_save import CheckLimitReached, verify_endpoint
from cloud_agent.application.model_connection_status import verification_status
from cloud_agent.application.model_connections import (
    model_connection_cipher,
    model_connections_enabled,
    new_connection_id,
)
from cloud_agent.crypto.credential_cipher import CredentialCipherError
from cloud_agent.domain.errors import Refused, Unavailable
from cloud_agent.domain.model_connections import (
    MAX_NAME_CHARS,
    ModelConnectionConflict,
    taken_model,
)
from cloud_agent.domain.model_sign_in import (
    CALLBACK_PATH,
    MODEL_SIGN_IN_SETTINGS,
    SignInProvider,
    configured_provider,
)
from cloud_agent.logging_utils import get_logger
from cloud_agent.mcp.http_body import IDENTITY_ENCODING_HEADERS, iter_response_bytes
from cloud_agent.mcp.transports import pinned_transport
from cloud_agent.settings import setting_env, text_setting
from cloud_agent.source_control.source_control import (
    OAuthStateError,
    SourceControlCipher,
    SourceControlError,
    consume_oauth_state,
    issue_oauth_state,
)
from cloud_agent_shared.model_connections import valid_api_key

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable

    from cloud_agent.ports import ModelConnectionRepository, SourceControlRepository

logger = get_logger(__name__)

#: The ``agent_oauth_states.provider`` of a sign-in, apart from every source-control one.
STATE_PROVIDER = "model-sign-in"
#: The verifier's own encryption domain under the user-credential key.
_VERIFIER_DOMAIN = b"cloud-agent/model-sign-in-verifier/v1"
_EXCHANGE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
#: A key exchange answers one short JSON object.
_MAX_EXCHANGE_BYTES = 64 * 1024
_MAX_CODE_CHARS = 2048


class SignInStorage(Protocol):
    """What a sign-in reads and writes: the flow's state, and the owner's connections."""

    #: Only its OAuth states: the flow's single-use state and verifier.
    source_control: SourceControlRepository
    model_connections: ModelConnectionRepository


def sign_in_provider() -> SignInProvider | None:
    """The provider as the settings configure it now, or ``None`` while one is missing."""
    return configured_provider(
        {setting.key: text_setting(setting.key) for setting in MODEL_SIGN_IN_SETTINGS}
    )


def _web_base_url() -> str:
    return (setting_env("AGENT_WEB_BASE_URL") or "").strip().rstrip("/")


def connected_to(provider: SignInProvider, rows: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The owner's live connection that already uses the provider's Base URL, if any."""
    for row in rows:
        if row.get("deleted_at") is None and row.get("base_url") == provider.base_url:
            return row
    return None


def sign_in_offer(rows: Iterable[dict[str, Any]]) -> SignInProvider | None:
    """The provider to offer an owner with these live connections, or ``None``.

    Offered while the feature is configured and on, the web app's address is
    known (the callback lives there), and the owner has no connection that
    already uses the provider's Base URL.
    """
    provider = sign_in_provider()
    if provider is None or not model_connections_enabled() or not _web_base_url():
        return None
    return None if connected_to(provider, rows) is not None else provider


def _verifier_cipher() -> SourceControlCipher:
    try:
        return SourceControlCipher(domain=_VERIFIER_DOMAIN)
    except SourceControlError:
        raise _unconfigured() from None


def _unconfigured() -> Unavailable:
    return Unavailable(
        "model_connections_unconfigured",
        "Model connections need the deployment's credential encryption key.",
    )


def _ready() -> SignInProvider:
    """The provider, when owners may sign in now; otherwise the refusal."""
    provider = sign_in_provider()
    if provider is None or not _web_base_url():
        raise Refused(
            "not_found",
            "model_sign_in_unavailable",
            "This deployment does not offer signing in at a model provider.",
        )
    if not model_connections_enabled():
        raise Refused(
            "forbidden",
            "model_connections_disabled",
            "This deployment has not turned on model connections.",
        )
    try:
        model_connection_cipher()
    except CredentialCipherError:
        raise _unconfigured() from None
    return provider


def _already_connected(provider: SignInProvider, row: dict[str, Any]) -> Refused:
    return Refused(
        "conflict",
        "model_sign_in_connected",
        f"Your connection {row['name']} already uses {provider.name}. Edit it to change its key.",
    )


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:128]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


async def start_sign_in(store: SignInStorage, *, user_id: str) -> str:
    """Issue the owner's state and verifier; answer where to send the browser."""
    provider = _ready()
    rows = await store.model_connections.list_model_connections(user_id=user_id)
    existing = connected_to(provider, rows)
    if existing is not None:
        raise _already_connected(provider, existing)
    verifier, challenge = _pkce_pair()
    state = await issue_oauth_state(
        store.source_control,
        user_id=user_id,
        provider=STATE_PROVIDER,
        cipher=_verifier_cipher(),
        code_verifier=verifier,
    )
    callback = f"{_web_base_url()}{CALLBACK_PATH}?{urlencode({'state': state})}"
    query = urlencode(
        {
            "callback_url": callback,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    logger.info("agent_model_sign_in_started", extra={"event": "agent_model_sign_in_started"})
    return f"{provider.authorize_url}?{query}"


def _unique_name(name: str, rows: list[dict[str, Any]]) -> str:
    """``name``, or the first ``name 2``, ``name 3``… no live connection of the owner has."""
    taken = {str(row["name"]).lower() for row in rows}
    if name.lower() not in taken:
        return name
    for number in range(2, 100):
        suffix = f" {number}"
        candidate = f"{name[: MAX_NAME_CHARS - len(suffix)]}{suffix}"
        if candidate.lower() not in taken:
            return candidate
    raise Refused(
        "conflict",
        "model_connection_name_taken",
        f"You already have connections named {name}. Rename one and try again.",
    )


def _free_models(provider: SignInProvider, rows: list[dict[str, Any]]) -> list[str]:
    """The configured models no other connection of the owner lists, or the refusal."""
    models = [model for model in provider.models if taken_model([model], rows) is None]
    if not models:
        raise Refused(
            "conflict",
            "model_sign_in_models_taken",
            f"Every model {provider.name} would add is already on another of your "
            "connections. Nothing was saved.",
        )
    return models


class SignInSaved:
    """The connection a sign-in saved, and the configured models it left off."""

    def __init__(self, row: dict[str, Any], skipped: list[str]) -> None:
        """Keep the saved row and the models that were not added to it."""
        self.row = row
        self.skipped = skipped


async def finish_sign_in(
    store: SignInStorage,
    *,
    user_id: str,
    code: str,
    state: str,
    resolve: Callable[[str, int], Awaitable[Iterable[str]]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SignInSaved:
    """Consume the owner's state, exchange the code and save the verified connection.

    Raises ``Refused`` or ``Unavailable``; ``CheckLimitReached``,
    ``VerificationTimedOut`` and ``VerificationRefused`` as a save does, which
    the route answers the same way.
    """
    provider = _ready()
    try:
        verifier = await consume_oauth_state(
            store.source_control,
            state=state,
            user_id=user_id,
            provider=STATE_PROVIDER,
            cipher=_verifier_cipher(),
        )
    except OAuthStateError:
        verifier = None
    except SourceControlError:
        raise _unconfigured() from None
    if not verifier:
        raise Refused(
            "invalid",
            "model_sign_in_expired",
            "This sign-in expired or was already used. Start again from the Models page.",
        )
    if not code or len(code) > _MAX_CODE_CHARS:
        raise Refused(
            "invalid",
            "model_sign_in_refused",
            f"{provider.name} did not return a usable code. Start again.",
        )
    rows = await store.model_connections.list_model_connections(user_id=user_id)
    existing = connected_to(provider, rows)
    if existing is not None:
        raise _already_connected(provider, existing)
    name = _unique_name(provider.name, rows)
    models = _free_models(provider, rows)
    if not await store.model_connections.reserve_model_connection_checks(
        user_id=user_id, count=len(models), limit=CHECKS_PER_HOUR, seconds=3600
    ):
        raise CheckLimitReached
    key = await exchange_code(
        provider, code=code, verifier=verifier, resolve=resolve, transport=transport
    )
    results = await verify_endpoint(
        base_url=provider.base_url, api_key=key, models=models, every=False
    )
    usable = [model for model in models if verification_status(model, results[model])["runtimes"]]
    try:
        row = await store.model_connections.create_model_connection(
            user_id=user_id,
            values={
                "id": new_connection_id(),
                "name": name,
                "base_url": provider.base_url,
                "models": usable,
                "api_key_ciphertext": model_connection_cipher().encrypt(key),
                "model_checks": {model: results[model] for model in usable},
            },
        )
    except ModelConnectionConflict:
        raise Refused(
            "conflict",
            "model_connection_name_taken",
            f"You already have a connection named {name}. Nothing was saved; try again.",
        ) from None
    logger.info(
        "agent_model_sign_in_saved",
        extra={"event": "agent_model_sign_in_saved", "models": len(usable)},
    )
    return SignInSaved(row, [model for model in provider.models if model not in usable])


def _exchange_refused(provider: SignInProvider) -> Refused:
    return Refused(
        "invalid",
        "model_sign_in_refused",
        f"{provider.name} did not accept this sign-in, so no key was issued. Start again.",
    )


def _exchange_unavailable(provider: SignInProvider) -> Unavailable:
    return Unavailable(
        "model_sign_in_unavailable_upstream",
        f"{provider.name} could not be reached to issue the key. Try again later.",
    )


async def exchange_code(
    provider: SignInProvider,
    *,
    code: str,
    verifier: str,
    resolve: Callable[[str, int], Awaitable[Iterable[str]]] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> str:
    """The API key the provider issues for this code and verifier, or the refusal.

    One JSON POST to the key URL, dialled to the address the egress policy
    checked, with no redirect followed and the answer read up to a bound.
    A 4xx is the provider declining this code (``Refused``); anything else
    that does not carry a key is an outage (``Unavailable``).
    """
    try:
        await guard_base_url(provider.key_url, resolve=resolve)
    except CheckStop:
        raise _exchange_unavailable(provider) from None
    body = {"code": code, "code_verifier": verifier, "code_challenge_method": "S256"}
    try:
        async with (
            httpx.AsyncClient(
                transport=transport or pinned_transport(),
                follow_redirects=False,
                trust_env=False,
                timeout=_EXCHANGE_TIMEOUT,
                headers=IDENTITY_ENCODING_HEADERS,
            ) as client,
            client.stream("POST", provider.key_url, json=body) as response,
        ):
            status = response.status_code
            chunks: list[bytes] = []
            total = 0
            async for chunk in iter_response_bytes(response):
                total += len(chunk)
                if total > _MAX_EXCHANGE_BYTES:
                    raise _exchange_unavailable(provider)
                chunks.append(chunk)
    except (httpx.HTTPError, OSError):
        raise _exchange_unavailable(provider) from None
    if 400 <= status < 500 and status not in (408, 429):
        logger.info(
            "agent_model_sign_in_refused",
            extra={"event": "agent_model_sign_in_refused", "http_status": status},
        )
        raise _exchange_refused(provider)
    if status != 200:
        logger.warning(
            "agent_model_sign_in_exchange_failed",
            extra={"event": "agent_model_sign_in_exchange_failed", "http_status": status},
        )
        raise _exchange_unavailable(provider)
    try:
        answer = json.loads(b"".join(chunks))
    except ValueError:
        answer = None
    key = answer.get("key") if isinstance(answer, dict) else None
    if not isinstance(key, str) or not valid_api_key(key.strip()):
        logger.warning(
            "agent_model_sign_in_exchange_failed",
            extra={"event": "agent_model_sign_in_exchange_failed", "http_status": status},
        )
        raise _exchange_unavailable(provider)
    return key.strip()


__all__ = [
    "STATE_PROVIDER",
    "SignInSaved",
    "SignInStorage",
    "connected_to",
    "exchange_code",
    "finish_sign_in",
    "sign_in_offer",
    "sign_in_provider",
    "start_sign_in",
]
