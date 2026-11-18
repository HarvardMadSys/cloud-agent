"""Shared retained-session lifecycle primitives."""

from __future__ import annotations

from cloud_agent_host.container.sandbox import SandboxError

DEFAULT_CHAT_IDLE_TIMEOUT_S = 30 * 60.0
DEFAULT_SWEEP_INTERVAL_S = 5.0


class SessionBusy(SandboxError):
    """Another fenced owner is already using this chat member."""


class SessionLeaseLost(SandboxError):
    """A local operation carried a stale or unknown activation lease."""


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
    "DEFAULT_CHAT_IDLE_TIMEOUT_S",
    "DEFAULT_SWEEP_INTERVAL_S",
    "SessionBusy",
    "SessionChanged",
    "SessionLeaseLost",
    "StaleStartFence",
    "conflict_kind",
]
