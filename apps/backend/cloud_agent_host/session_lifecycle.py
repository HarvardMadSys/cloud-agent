"""Shared retained-session lifecycle primitives."""

from __future__ import annotations

import time

from cloud_agent_host.container.sandbox import SandboxError

DEFAULT_CHAT_IDLE_TIMEOUT_S = 30 * 60.0
DEFAULT_SWEEP_INTERVAL_S = 5.0


class SessionBusy(SandboxError):
    """Another fenced owner is already using this chat member."""

    #: What the refused acquire had already done to the container; see
    #: :data:`CONTAINER_ACTION_HEADER`. ``None`` outside an acquire.
    container_action: str | None = None


class SessionLeaseLost(SandboxError):
    """A local operation carried a stale or unknown activation lease."""

    #: As :attr:`SessionBusy.container_action`.
    container_action: str | None = None


class SessionChanged(SessionLeaseLost):
    """The group moved on while a request worked outside the lock; the request still stands.

    Another operation began or ended on the group (a lifecycle check, a stop,
    an archive) between this request's observation and its admission. The
    group and the member are the ones the request named and nothing it owned
    was taken, so the same request, made again, observes the group afresh.
    """


class StaleStartFence(SessionLeaseLost):
    """A request would start a stopped group under a start fence already sealed.

    A checkpoint of the group's last stop may already be the one it moves
    with, and it names that fence: a start under it would change the group
    after its checkpoint was taken. The control plane fences again and retries.
    """


#: The header a sandbox manager's 409 carries to say which refusal it is.
#: The detail is prose for people; this is what a caller branches on.
CONFLICT_HEADER = "X-Sandbox-Conflict"
#: :class:`SessionBusy`: another owner or operation holds the member or group now.
CONFLICT_BUSY = "busy"
#: :class:`SessionChanged`: the group moved on during the request; ask again.
CONFLICT_CHANGED = "changed"
#: Any other :class:`SessionLeaseLost`: the request's lease or fence is stale.
CONFLICT_LEASE_LOST = "lease_lost"
CONFLICTS = frozenset({CONFLICT_BUSY, CONFLICT_CHANGED, CONFLICT_LEASE_LOST})

#: The header a refused acquire's 409 carries to say what that request had
#: already done to the group's container: ``create``, ``start`` or ``reuse``
#: (``SandboxActionFacts``' literals), or ``none``. A repeat of the request
#: finds the container the refused one created and reports only ``reuse``, so
#: the caller keeps this to know what the whole acquire did.
CONTAINER_ACTION_HEADER = "X-Sandbox-Container-Action"
#: The refused request did nothing to the container.
CONTAINER_ACTION_NONE = "none"
CONTAINER_ACTIONS = frozenset({"create", "start", "reuse", CONTAINER_ACTION_NONE})
#: Longest a sandbox manager request waits at any one conflict, unless the
#: caller asks for less (``conflict_wait_s``).
CONFLICT_WAIT_S = 30.0


def conflict_deadline(shared: float | None) -> float:
    """When one conflict wait gives up: at ``shared``, or :data:`CONFLICT_WAIT_S` from now.

    ``shared`` is the deadline a request's caller set for every wait the
    request meets (``conflict_wait_s``); without one, each wait has its own.
    """
    return shared if shared is not None else time.monotonic() + CONFLICT_WAIT_S


def conflict_kind(exc: SessionBusy | SessionLeaseLost) -> str:
    """The :data:`CONFLICT_HEADER` value that names ``exc``."""
    if isinstance(exc, SessionBusy):
        return CONFLICT_BUSY
    if isinstance(exc, SessionChanged):
        return CONFLICT_CHANGED
    return CONFLICT_LEASE_LOST


__all__ = [
    "CONFLICTS",
    "CONFLICT_BUSY",
    "CONFLICT_CHANGED",
    "CONFLICT_HEADER",
    "CONFLICT_LEASE_LOST",
    "CONFLICT_WAIT_S",
    "CONTAINER_ACTIONS",
    "CONTAINER_ACTION_HEADER",
    "CONTAINER_ACTION_NONE",
    "DEFAULT_CHAT_IDLE_TIMEOUT_S",
    "DEFAULT_SWEEP_INTERVAL_S",
    "SessionBusy",
    "SessionChanged",
    "SessionLeaseLost",
    "StaleStartFence",
    "conflict_deadline",
    "conflict_kind",
]
