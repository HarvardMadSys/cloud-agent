"""Models the deployment provides: what an owner is offered, and closing what a change left behind.

An administrator configures a *provided* connection: a provider's Base URL and
the deployment's own key, and it lists what the provider lists for that key.
Every admitted owner may run turns on it with no setup; the provider's own
budget on the key is what limits them. Turns
resolve it through :mod:`cloud_agent.application.model_connections`, like an
owner's own connection; the model relay (:mod:`cloud_agent.api.model_relay`)
is what holds the key. This module is the rest: what the composer is told, and
what happens to running processes when the connection changes.

See ``docs/agents/specs/2026-11-12-platform-model-relay.zh-CN.md``.
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any, Protocol

from cloud_agent.application.model_connection_status import model_efforts, usable_runtimes
from cloud_agent.application.residency import close_released_residencies
from cloud_agent.domain.operator_models import first_usable_model
from cloud_agent.logging_utils import get_logger

if TYPE_CHECKING:
    from cloud_agent.ports import HostPoolRepository, OperatorModelRepository

logger = get_logger(__name__)


class OperatorModelStorage(Protocol):
    """What reading and invalidating a provided connection reaches for."""

    operator_models: OperatorModelRepository
    host_pool: HostPoolRepository


def new_operator_connection_id() -> str:
    """A fresh id for a provided connection."""
    return f"oconn_{secrets.token_hex(16)}"


async def provided_sources(operator_models: OperatorModelRepository) -> list[dict[str, Any]]:
    """Every provided connection as an owner's composer offers it, oldest first.

    Each enabled connection with the models an agent can use, in the order
    its provider lists them; one with no such model is not listed. Every
    admitted owner is offered the same.
    """
    sources: list[dict[str, Any]] = []
    for row in await operator_models.enabled_operator_connections():
        runtimes = usable_runtimes(row)
        models = [model for model in row["models"] if runtimes.get(model)]
        if not models:
            continue
        sources.append(
            {
                "id": row["id"],
                "name": row["name"],
                "models": models,
                "enabled": True,
                "model_runtimes": {model: runtimes[model] for model in models},
                "model_efforts": model_efforts({**row, "models": models}),
            }
        )
    return sources


def provided_default(sources: list[dict[str, Any]]) -> tuple[str, str] | None:
    """``(model, connection_id)`` an owner with no history starts on, when there is one.

    The oldest provided connection's first model an agent can use.
    """
    for source in sources:
        model = first_usable_model(source["models"], source["model_runtimes"])
        if model is not None:
            return model, str(source["id"])
    return None


async def invalidate_operator_connection(store: OperatorModelStorage, *, connection_id: str) -> int:
    """Close every member process still running on an older state of a provided connection.

    Called after the change committed, as for an owner's connection: a
    residency bound before it is found here, and one bound after was bound at
    the new revision or refused. Only a new Base URL, disabling and deleting
    leave anything to close; a rotated key does not, since no process holds
    the key.
    """
    released = await store.operator_models.release_stale_operator_residencies(
        connection_id=connection_id
    )
    if released:
        logger.info(
            "agent_operator_model_connection_residencies_released",
            extra={
                "event": "agent_operator_model_connection_residencies_released",
                "connection_id": connection_id,
                "count": len(released),
            },
        )
        await close_released_residencies(store.host_pool, released)
    return len(released)


__all__ = [
    "OperatorModelStorage",
    "invalidate_operator_connection",
    "new_operator_connection_id",
    "provided_default",
    "provided_sources",
]
