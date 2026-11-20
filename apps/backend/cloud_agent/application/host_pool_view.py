"""The runner pool as an administrator reads it, built from one repository read.

Both ways of reaching the pool answer with this document: the admin API
(``GET /admin/agent/runner-hosts`` and every pool action) and the deployment's
drain command (:mod:`cloud_agent.deployment.host_fence`). A deployment therefore
reads the same fields whichever way it drains a host.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from cloud_agent.domain.execution_profiles import KATA_RUNTIME, default_execution_profile
from cloud_agent.domain.host_pool import HOST_ONLINE
from cloud_agent.schemas import (
    AgentRunnerHost,
    ListAgentRunnerHostsResponse,
    host_setting_definitions,
)
from cloud_agent.schemas.capacity_protocol import admin_capacity_view
from cloud_agent_shared.host_settings import admin_values, reported_values

if TYPE_CHECKING:
    from cloud_agent.ports import HostPoolRepository


def untrusted_without_disk_quota(row: dict[str, Any]) -> bool:
    """Whether a host that can take untrusted work reports applying no disk quota.

    Only the Kata runtime is handed ``untrusted-kata-v1`` work, so only it is
    named. The applied report is the host's own word for what a new sandbox
    gets; a host that has not reported the setting is not guessed about.
    """
    if row.get("sandbox_runtime") != KATA_RUNTIME:
        return False
    applied = reported_values(row.get("settings_applied")) or {}
    return applied.get("sandbox_disk_mib") == 0


def host_item(row: dict[str, Any], *, now: datetime | None = None) -> AgentRunnerHost:
    """One pool row as the admin page shows it, its ages measured from ``now``."""
    last_seen: datetime = row["last_seen_at"]
    read_at = datetime.now(UTC) if now is None else now
    # The column is TIMESTAMPTZ, so asyncpg hands back an aware datetime; a
    # naive one would only come from a hand-built row in a test.
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)
    epoch = row.get("capacity_epoch")
    moving_out = row.get("removal_requested_at") is not None and bool(row.get("removal_moves_out"))
    return AgentRunnerHost(
        host=row["host"],
        active=bool(row["is_active"]),
        last_worker_id=row.get("last_worker_id"),
        first_seen_at=row["first_seen_at"],
        last_seen_at=last_seen,
        seconds_since_seen=max(0.0, (read_at - last_seen).total_seconds()),
        # Read with ``.get`` and defaulted, not indexed. A row predating the
        # pool columns — and any caller that builds one by hand — must render,
        # because the alternative is that the page an operator uses to fix a
        # bad host is the page that 500s on it.
        status=row.get("status") or HOST_ONLINE,
        enrolled=bool(row.get("enrolled")),
        labels=row.get("labels") or {},
        capabilities=row.get("capabilities") or [],
        slots_total=row.get("slots_total"),
        slots_used=row.get("slots_used") or 0,
        slots_desired=row.get("slots_desired"),
        launch_error=row.get("launch_error"),
        runner_version=row.get("runner_version"),
        sandbox_manager_url=row.get("sandbox_manager_url"),
        last_heartbeat_at=row.get("last_heartbeat_at"),
        drain_fence_token=row.get("drain_fence_token"),
        drain_fence_status_before=row.get("drain_fence_status_before"),
        drain_fence_active=bool(row.get("drain_fence_active")),
        # Unlike the phase-G display fields above, this is a required protocol
        # generation. The migration backfills it before this API may start;
        # silently defaulting it would make a partial CAS contract look usable.
        drain_fence_epoch=row["drain_fence_epoch"],
        removal_pending=row.get("removal_requested_at") is not None,
        removal_moves_out=moving_out,
        sandboxes_left=int(row.get("sandboxes_left") or 0) if moving_out else None,
        sandboxes_unmovable=int(row.get("sandboxes_unmovable") or 0) if moving_out else None,
        removal_leaves_unmoved=moving_out and bool(row.get("removal_leaves_unmoved")),
        sandbox_runtime=row.get("sandbox_runtime"),
        sandbox_image_digest=row.get("sandbox_image_digest"),
        runtime_versions=row.get("runtime_versions"),
        runtime_versions_available=row.get("runtime_versions_available"),
        capacity_epoch=None if epoch is None else str(epoch),
        # The stored block is re-validated rather than trusted: one corrupt
        # document costs that host its capacity numbers, not the whole page.
        capacity=admin_capacity_view(
            snapshot=row.get("capacity_snapshot"),
            received_at=row.get("capacity_received_at"),
            sampled_at=row.get("capacity_sampled_at"),
            now=read_at,
        ),
        # Through the same filter the heartbeat answer uses, so the page never
        # shows a value the host would not be sent.
        settings_desired=admin_values(row.get("settings_desired")),
        settings_applied=reported_values(row.get("settings_applied")),
        settings_local=reported_values(row.get("settings_local")),
        sandbox_archive_settings_applied_revision=row.get(
            "sandbox_archive_settings_applied_revision"
        ),
        untrusted_without_disk_quota=untrusted_without_disk_quota(row),
    )


async def pool_snapshot(store: HostPoolRepository) -> ListAgentRunnerHostsResponse:
    """The pool as it stands — the response every endpoint here returns.

    Every part comes from one read. ``active_host`` is the policy's own value
    rather than something derived from the list: where the two could disagree —
    a pinned host deleted out from under the policy — the honest answer is the
    name the claim gate is actually enforcing, not "unpinned" while the queue
    sits still. ``runtime_versions`` is the same fact about versions, and is
    read in the same transaction as the menus it must be displayed against.

    ``observed_at`` is that transaction's own instant. Every age on the page is
    measured from it rather than from whenever the response happened to be
    serialized, so a slow request cannot make the whole fleet look older than
    it is.
    """
    rows, active, selected_versions, observed_at = await store.runner_pool()
    # Read beside the pool rather than inside its snapshot. A global value
    # saved between the two reads is the ordinary state of a setting hosts
    # have not applied yet, which every host row already reports as such.
    global_settings = await store.host_settings()
    return ListAgentRunnerHostsResponse(
        execution_profile=default_execution_profile().value,
        hosts=[host_item(row, now=observed_at) for row in rows],
        active_host=active,
        runtime_versions=selected_versions,
        host_settings=admin_values(global_settings),
        host_setting_definitions=host_setting_definitions(),
        observed_at=observed_at,
    )
