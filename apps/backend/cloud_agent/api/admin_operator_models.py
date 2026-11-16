"""Admin routes for the models this deployment provides.

A provided connection is the deployment's own inference endpoint: a name, a
Base URL and the deployment's key, offered to every admitted owner without
any setup of theirs. Its models are the ones the provider lists for that key,
in the provider's order, with the prices that listing gives: the provider,
not this form, decides what the deployment offers, and an administrator
limits it by limiting the key. Creating one, and an edit that changes its key
or Base URL, reads the listing and verifies every model inside the same
request, and writes nothing unless at least one has an agent that can use
it; a model no agent can use is kept with its facts and offered to nobody.
Refreshing reads the listing again with the stored key.

The key is write-only, encrypted under the settings root key in a domain of
its own, and leaves the control plane in no claim, residency, response, log
or audit row: turns reach the provider through the model relay. Nothing here
limits what an owner may use; the provider's own budget on the key does.

A new connection is disabled. A new Base URL, disabling and deleting close
every member process still running on the old state. A new key does not: no
process holds the key.

Every write is recorded in the admin audit trail; a key is never in it.
"""

from __future__ import annotations

import hmac
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import Field

from cloud_agent.api.deps import (
    get_client_ip,
    get_storage,
    log_admin_action,
    require_store,
    verify_admin_access,
)
from cloud_agent.api.model_connections import (
    _VERIFY_RESPONSES,
    ModelStatusView,
    _ConnectionRoute,
    _Fields,
    _refused,
)
from cloud_agent.application.model_connection_check import (
    CHECK_MESSAGES,
    CheckStop,
    ListedModel,
    discover_listing,
)
from cloud_agent.application.model_connection_save import (
    VerificationRefused,
    VerificationTimedOut,
    kept_checks,
    plan_edit,
    verify_endpoint,
)
from cloud_agent.application.model_connection_status import connection_statuses
from cloud_agent.application.model_connections import (
    model_connections_enabled,
    operator_connection_cipher,
)
from cloud_agent.application.operator_models import (
    invalidate_operator_connection,
    new_operator_connection_id,
)
from cloud_agent.crypto.credential_cipher import CredentialCipherError
from cloud_agent.domain.model_connections import ModelConnectionConflict
from cloud_agent.domain.operator_models import listed_prices
from cloud_agent.schemas import (
    OperatorModelConnectionList,
    OperatorModelConnectionView,
    OperatorModelPrices,
    OperatorModelUsageResponse,
    OperatorModelUsageRow,
)
from cloud_agent_shared.model_connections import (
    MAX_API_KEY_CHARS,
    MAX_MODELS,
    is_operator_connection_id,
)

if TYPE_CHECKING:
    from cloud_agent.application.model_connection_save import EditPlan
    from cloud_agent.domain.operator_models import ModelPrices
    from cloud_agent.ports import (
        AuditRepository,
        HostPoolRepository,
        LocalAccountRepository,
        OperatorModelRepository,
    )

#: The most usage rows one answer carries, and the longest range it covers.
_MAX_USAGE_ROWS = 2000
_MAX_USAGE_DAYS = 92


class _Storage(Protocol):
    audit: AuditRepository
    host_pool: HostPoolRepository
    local_accounts: LocalAccountRepository
    operator_models: OperatorModelRepository


router = APIRouter(
    prefix="/admin/agent/operator-model-connections",
    tags=["operator-model-connections"],
    route_class=_ConnectionRoute,
    dependencies=[Depends(verify_admin_access)],
)


class OperatorModelConnectionCreate(_Fields):
    """A new provided connection: its models are what the provider lists for the key."""

    name: str = Field(..., max_length=200)
    base_url: str = Field(..., max_length=1024)
    #: Write-only: encrypted before it is stored and never answered.
    api_key: str = Field(..., max_length=MAX_API_KEY_CHARS + 64, repr=False)


class OperatorModelConnectionUpdate(_Fields):
    """Change some fields; an omitted field keeps its value. ``api_key`` rotates the key.

    A new key or Base URL reads the provider's listing again and verifies
    every model it lists before anything is saved.
    """

    name: str | None = Field(None, max_length=200)
    base_url: str | None = Field(None, max_length=1024)
    api_key: str | None = Field(None, max_length=MAX_API_KEY_CHARS + 64, repr=False)
    enabled: bool | None = None


def _view(row: dict[str, Any], prices: dict[str, ModelPrices]) -> OperatorModelConnectionView:
    return OperatorModelConnectionView(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        models=list(row["models"]),
        enabled=bool(row["enabled"]),
        prices={
            model: OperatorModelPrices(
                input_price=price.input,
                output_price=price.output,
                cache_read_price=price.cache_read,
                cache_write_price=price.cache_write,
            )
            for model, price in prices.items()
            if model in row["models"]
        },
        model_status=[
            ModelStatusView(**status).model_dump() for status in connection_statuses(row)
        ],
        key_refused_at=row.get("key_refused_at"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


async def _answer(store: _Storage, row: dict[str, Any]) -> OperatorModelConnectionView:
    prices = await store.operator_models.operator_prices(connection_ids=[row["id"]])
    return _view(row, prices[row["id"]])


def _cipher() -> Any:
    try:
        return operator_connection_cipher()
    except CredentialCipherError:
        raise _refused(
            503,
            "operator_model_connections_unconfigured",
            "Provided models need the deployment's settings encryption key.",
        ) from None


async def _live(store: _Storage, connection_id: str) -> dict[str, Any]:
    row = (
        await store.operator_models.get_operator_connection(connection_id=connection_id)
        if is_operator_connection_id(connection_id)
        else None
    )
    if row is None or row.get("deleted_at") is not None:
        raise _refused(
            404, "operator_model_connection_not_found", "Provided model connection not found."
        )
    return row


def _require_enabled() -> None:
    if not model_connections_enabled():
        raise _refused(
            403,
            "model_connections_disabled",
            "This deployment has model connections switched off.",
        )


def _taken(name: str) -> HTTPException:
    return _refused(
        409,
        "operator_model_connection_name_taken",
        f"A provided connection is already named {name}.",
    )


def _changed() -> HTTPException:
    return _refused(
        409,
        "operator_model_connection_changed",
        "The connection changed while this was being saved. Nothing was saved; try again.",
    )


async def _name_taken(store: _Storage, name: str, *, other_than: str = "") -> bool:
    rows = await store.operator_models.list_operator_connections()
    return any(row["name"].lower() == name.lower() and row["id"] != other_than for row in rows)


async def _listing(*, base_url: str, api_key: str) -> list[ListedModel]:
    """What the provider lists for this key, or the refusal that saves nothing.

    A provider whose key lists more than ``MAX_MODELS`` models is refused
    rather than cut short: which ones to offer is the key's to say.
    """
    try:
        listed = await discover_listing(base_url=base_url, api_key=api_key)
    except CheckStop as stop:
        raise _refused(
            422,
            "operator_model_listing_failed",
            "The provider's model list could not be read with this key: "
            + CHECK_MESSAGES[stop.category],
        ) from None
    except TimeoutError:
        raise _refused(
            422,
            "operator_model_listing_failed",
            "The provider's model list could not be read with this key: "
            + CHECK_MESSAGES["unreachable"],
        ) from None
    if not listed:
        raise _refused(
            422, "operator_model_listing_empty", "The provider lists no model for this key."
        )
    if len(listed) > MAX_MODELS:
        raise _refused(
            422,
            "operator_model_listing_too_long",
            f"The provider lists {len(listed)} models for this key, and a provided "
            f"connection offers at most {MAX_MODELS}. Limit the key at the provider to "
            "the models this deployment should provide.",
        )
    return listed


def _prices(listed: list[ListedModel]) -> dict[str, ModelPrices | None]:
    return {entry.id: listed_prices(entry.pricing) for entry in listed}


async def _verified(*, base_url: str, api_key: str, models: list[str]) -> dict[str, dict[str, Any]]:
    """Every model's facts at this endpoint, or the HTTP refusal that saves nothing.

    Refused only when no model has an agent that can use it.
    """
    try:
        return await verify_endpoint(base_url=base_url, api_key=api_key, models=models, every=False)
    except VerificationTimedOut:
        raise _refused(
            504,
            "model_connection_verification_timeout",
            "Verification took too long. Nothing was saved; check the Base URL and try again.",
        ) from None
    except VerificationRefused as refused:
        raise HTTPException(
            422,
            {
                "error": {
                    "type": "model_connection_verification_failed",
                    "message": "No model this key lists could be verified for any agent. "
                    "Nothing was saved.",
                    "models": [
                        ModelStatusView(**status).model_dump() for status in refused.statuses
                    ],
                }
            },
        ) from None


async def _audit(
    request: Request,
    store: _Storage,
    actor: str,
    action: str,
    row: dict[str, Any],
    details: dict[str, Any] | None = None,
) -> None:
    await log_admin_action(
        store,
        get_client_ip(request),
        action,
        None,
        {"connection_id": row["id"], "name": row["name"], **(details or {})},
        actor_id=actor,
    )


@router.get("", response_model=OperatorModelConnectionList)
async def list_connections(
    store: _Storage | None = Depends(get_storage),
) -> OperatorModelConnectionList:
    """Every provided connection, with its models, their prices and their verification."""
    store = require_store(store)
    rows = await store.operator_models.list_operator_connections()
    prices = await store.operator_models.operator_prices(connection_ids=[row["id"] for row in rows])
    return OperatorModelConnectionList(
        connections=[_view(row, prices[row["id"]]) for row in rows],
        relay_hosts=await store.operator_models.model_relay_host_count(),
        feature_enabled=model_connections_enabled(),
    )


@router.post(
    "",
    response_model=OperatorModelConnectionView,
    status_code=201,
    responses=_VERIFY_RESPONSES,
)
async def create_connection(
    request: Request,
    body: OperatorModelConnectionCreate,
    actor: str = Depends(verify_admin_access),
    store: _Storage | None = Depends(get_storage),
) -> OperatorModelConnectionView:
    """Read what the key lists, verify it, then add the connection, disabled.

    Nothing is stored when the listing cannot be read, lists nothing or more
    than ``MAX_MODELS``, or no model it lists has an agent that can use it.
    """
    _require_enabled()
    store = require_store(store)
    cipher = _cipher()
    if await _name_taken(store, body.name):
        raise _taken(body.name)
    listed = await _listing(base_url=body.base_url, api_key=body.api_key)
    models = [entry.id for entry in listed]
    checks = await _verified(base_url=body.base_url, api_key=body.api_key, models=models)
    try:
        row = await store.operator_models.create_operator_connection(
            values={
                "id": new_operator_connection_id(),
                "name": body.name,
                "base_url": body.base_url,
                "models": models,
                "api_key_ciphertext": cipher.encrypt(body.api_key),
                "model_checks": checks,
            }
        )
    except ModelConnectionConflict:
        raise _taken(body.name) from None
    await store.operator_models.set_operator_prices(connection_id=row["id"], prices=_prices(listed))
    await _audit(
        request,
        store,
        actor,
        "agent.operator_model_connection.create",
        row,
        {"base_url": row["base_url"], "models": row["models"]},
    )
    return await _answer(store, row)


def _same_key(row: dict[str, Any], key: str) -> bool:
    """Whether ``key`` is the stored one, so re-entering it rotates nothing."""
    try:
        stored = _cipher().decrypt(str(row["api_key_ciphertext"]))
    except (CredentialCipherError, HTTPException):
        return False
    return hmac.compare_digest(stored.encode(), key.encode())


@router.patch(
    "/{connection_id}", response_model=OperatorModelConnectionView, responses=_VERIFY_RESPONSES
)
async def update_connection(
    request: Request,
    connection_id: str,
    body: OperatorModelConnectionUpdate,
    actor: str = Depends(verify_admin_access),
    store: _Storage | None = Depends(get_storage),
) -> OperatorModelConnectionView:
    """Rename, rotate the key of, move, enable or disable a provided connection.

    A new key or Base URL reads the provider's listing with it and verifies
    every model it lists; the edit is committed only if that passes and the
    connection did not change meanwhile (409). A field sent with the value it
    already has is not a change.
    """
    store = require_store(store)
    row = await _live(store, connection_id)
    new_key = (
        body.api_key if body.api_key is not None and not _same_key(row, body.api_key) else None
    )
    requested: dict[str, Any] = body.model_dump(exclude_unset=True, include={"name", "base_url"})
    listed: list[ListedModel] | None = None
    base_url = requested.get("base_url") or row["base_url"]
    if new_key is not None or base_url != row["base_url"]:
        _require_enabled()
        listed = await _listing(
            base_url=base_url, api_key=new_key if new_key is not None else _key(row)
        )
        requested["models"] = [entry.id for entry in listed]
    plan = plan_edit(row, requested=requested, new_key=new_key)
    settings = (
        {"enabled": body.enabled}
        if body.enabled is not None and body.enabled != bool(row["enabled"])
        else {}
    )
    if settings.get("enabled") is True:
        _require_enabled()
    name = plan.changes.get("name")
    if name is not None and await _name_taken(store, name, other_than=row["id"]):
        raise _taken(name)
    try:
        updated = await _commit(store, row, plan, settings)
    except ModelConnectionConflict:
        raise _taken(str(name)) from None
    if listed is not None:
        await store.operator_models.set_operator_prices(
            connection_id=connection_id, prices=_prices(listed)
        )
    await invalidate_operator_connection(store, connection_id=connection_id)
    await _audit_update(request, store, actor, updated, plan, settings)
    return await _answer(store, updated)


@router.post(
    "/{connection_id}/refresh-models",
    response_model=OperatorModelConnectionView,
    responses=_VERIFY_RESPONSES,
)
async def refresh_models(
    request: Request,
    connection_id: str,
    actor: str = Depends(verify_admin_access),
    store: _Storage | None = Depends(get_storage),
) -> OperatorModelConnectionView:
    """Read the provider's listing again with the stored key, and follow it.

    A model the key now lists is verified and added, one it no longer lists
    is removed, and every model's prices are read again. For a key whose
    models were changed at the provider. Nothing is saved unless the listing
    could be read and every added model was verified.
    """
    _require_enabled()
    store = require_store(store)
    row = await _live(store, connection_id)
    listed = await _listing(base_url=row["base_url"], api_key=_key(row))
    plan = plan_edit(row, requested={"models": [entry.id for entry in listed]}, new_key=None)
    updated = await _commit(store, row, plan, {})
    await store.operator_models.set_operator_prices(
        connection_id=connection_id, prices=_prices(listed)
    )
    await _audit_update(request, store, actor, updated, plan, {})
    return await _answer(store, updated)


async def _commit(
    store: _Storage, row: dict[str, Any], plan: EditPlan, settings: dict[str, Any]
) -> dict[str, Any]:
    """Apply an edit: what it adds is verified first, the rest needs no provider call.

    Enabling is applied after a verified part committed.
    """
    connection_id = row["id"]
    changes = {**plan.changes, **settings}
    if plan.verify:
        changes = {name: value for name, value in changes.items() if name not in settings}
        models = list(plan.changes.get("models", row["models"]))
        fresh = await _verified(
            base_url=plan.changes.get("base_url", row["base_url"]),
            api_key=plan.new_key if plan.new_key is not None else _key(row),
            models=plan.verify,
        )
        checks = fresh if plan.endpoint_changed else {**kept_checks(row, models), **fresh}
        if plan.new_key is not None:
            changes["api_key_ciphertext"] = _cipher().encrypt(plan.new_key)
        updated = await store.operator_models.commit_verified_operator_update(
            connection_id=connection_id, expected=row, changes=changes, model_checks=checks
        )
        changes = settings
    else:
        updated = row
    if updated is not None and changes:
        updated = await store.operator_models.update_operator_connection(
            connection_id=connection_id, changes=changes
        )
    if updated is None:
        await _live(store, connection_id)
        raise _changed()
    return updated


def _key(row: dict[str, Any]) -> str:
    try:
        return str(_cipher().decrypt(row["api_key_ciphertext"]))
    except CredentialCipherError:
        raise _refused(
            503, "operator_model_connections_unconfigured", "The stored key cannot be read."
        ) from None


async def _audit_update(
    request: Request,
    store: _Storage,
    actor: str,
    row: dict[str, Any],
    plan: EditPlan,
    settings: dict[str, Any],
) -> None:
    """One row per kind of change: the key, enabling or disabling, and everything else."""
    if plan.new_key is not None:
        await _audit(
            request,
            store,
            actor,
            "agent.operator_model_connection.rotate_key",
            row,
            {"api_key": "[redacted]"},
        )
    if "enabled" in settings:
        await _audit(
            request,
            store,
            actor,
            "agent.operator_model_connection.enable"
            if settings["enabled"]
            else "agent.operator_model_connection.disable",
            row,
        )
    changed = sorted(set(plan.changes) - {"enabled"})
    if changed:
        await _audit(
            request,
            store,
            actor,
            "agent.operator_model_connection.update",
            row,
            {"changed": changed},
        )


@router.delete("/{connection_id}", status_code=204)
async def delete_connection(
    request: Request,
    connection_id: str,
    actor: str = Depends(verify_admin_access),
    store: _Storage | None = Depends(get_storage),
) -> Response:
    """Delete a provided connection: its key is erased and its running processes are closed.

    Turns that name it are refused from now on, never moved elsewhere.
    """
    store = require_store(store)
    row = await _live(store, connection_id)
    await store.operator_models.delete_operator_connection(connection_id=connection_id)
    await invalidate_operator_connection(store, connection_id=connection_id)
    await _audit(request, store, actor, "agent.operator_model_connection.delete", row)
    return Response(status_code=204)


async def _emails(store: _Storage, account_ids: list[str]) -> dict[str, str]:
    """Each account's email where it has one; an unreadable directory answers none."""
    if not account_ids:
        return {}
    try:
        return await store.local_accounts.account_emails(sorted(set(account_ids)))
    except Exception:
        return {}


@router.get("/{connection_id}/usage", response_model=OperatorModelUsageResponse)
async def usage(
    connection_id: str,
    start: date | None = Query(None, alias="from"),
    end: date | None = Query(None, alias="to"),
    store: _Storage | None = Depends(get_storage),
) -> OperatorModelUsageResponse:
    """What each account used of each model per UTC day, and what it cost.

    Read outside the sandbox, from the provider's own answers: the requests it
    served, the tokens each answer said it used, and those tokens at the
    model's listed prices at the time. Not the provider's bill. The last seven days
    when no range is given, and at most ``_MAX_USAGE_DAYS`` of them.
    """
    store = require_store(store)
    await _live(store, connection_id)
    today = datetime.now(UTC).date()
    last = end or today
    first = start or last - timedelta(days=6)
    if first > last or (last - first).days >= _MAX_USAGE_DAYS:
        raise _refused(
            422, "invalid_usage_range", f"Choose a range of at most {_MAX_USAGE_DAYS} days."
        )
    rows = await store.operator_models.operator_usage(
        connection_id=connection_id, start=first, end=last, limit=_MAX_USAGE_ROWS + 1
    )
    page = rows[:_MAX_USAGE_ROWS]
    emails = await _emails(store, [str(row["user_id"]) for row in page])
    return OperatorModelUsageResponse(
        rows=[
            OperatorModelUsageRow(
                day=row["day"],
                account_id=str(row["user_id"]),
                email=emails.get(str(row["user_id"]), ""),
                model=str(row["model"]),
                requests=int(row["requests"]),
                cost_microusd=int(row["cost_microusd"]),
                input_tokens=int(row["input_tokens"]),
                cache_read_tokens=int(row["cache_read_tokens"]),
                cache_write_tokens=int(row["cache_write_tokens"]),
                output_tokens=int(row["output_tokens"]),
                estimated_requests=int(row["estimated_requests"]),
            )
            for row in page
        ],
        truncated=len(rows) > _MAX_USAGE_ROWS,
    )
