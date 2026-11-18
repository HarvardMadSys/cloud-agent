"""Host-wide settings and the sandbox archive settings."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from cloud_agent.domain.errors import Refused
from cloud_agent.domain.host_pool import HOST_DISABLED
from cloud_agent.domain.sandbox_group import GroupState
from cloud_agent.sandbox_archive.sandbox_archive_credentials import (
    archive_settings_payload,
    seal_archive_storage,
    stored_archive_policy,
)
from cloud_agent.storage._sql.hosts import UNDELETED_GROUP_SQL
from cloud_agent.storage._sql.rows import load_json
from cloud_agent.storage._sql.runner_policy import archive_settings
from cloud_agent.storage.repositories.host_pool._common import HostPoolBase, _changed_settings
from cloud_agent_shared.sandbox_archive_settings import (
    SANDBOX_ARCHIVE_BUCKET,
    SANDBOX_ARCHIVE_DESTINATION_CAPABILITY,
    SandboxArchivePolicy,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from typing import Any

#: The ``error.type`` of a refused bucket change.
ARCHIVE_BUCKET_LOCKED = "archive_bucket_locked"

#: What the current bucket holds that is read back from it: the checkpoints of
#: groups that may still be restored, and the environment snapshots that are
#: offered (``superseded`` ones during their grace period) or being built,
#: whose upload goes to the bucket in force when the build started.
_STORED_IN_BUCKET_SQL = f"""
SELECT
    (SELECT count(*) FROM agent_sandbox_checkpoints c
     JOIN agent_sandboxes g ON g.id = c.sandbox_id
     WHERE {UNDELETED_GROUP_SQL} AND g.state <> {GroupState.LOST.sql}) AS checkpoints,
    (SELECT count(*) FROM agent_environment_snapshots
     WHERE state IN ('building', 'ready', 'superseded')) AS snapshots
"""

#: Hosts not removed that cannot be sent a bucket other than the default.
_HOSTS_WITHOUT_DESTINATION_SQL = """
SELECT host FROM agent_runner_hosts
WHERE status <> $1 AND NOT COALESCE(capabilities, '[]'::jsonb) ? $2
ORDER BY host
"""


def _counted(count: int, one: str, many: str) -> str:
    return f"1 {one}" if count == 1 else f"{count} {many}"


def _named(hosts: list[str], limit: int = 5) -> str:
    named = ", ".join(hosts[:limit])
    return named if len(hosts) <= limit else f"{named} and {len(hosts) - limit} more"


async def _refuse_bucket_change(
    conn: Any, *, current: SandboxArchivePolicy, requested: SandboxArchivePolicy
) -> None:
    """Refuse a bucket change that would strand stored objects or archive elsewhere.

    The bucket is set once (#1014). Checkpoint and snapshot rows record a key
    and an object version but no bucket, and restores and snapshot loads read
    the bucket in force now, so once one of them exists the bucket stays; a
    new deployment still chooses its own, and a mistyped one can be corrected
    until the first lands. Moving a deployment to another bucket is not
    supported. Separately, a host without the destination capability is sent
    no enabled policy in another bucket: it takes no work, and its sandbox
    manager keeps archiving into the bucket it last applied.

    The caller holds the policy row. An object a host uploaded before the
    change and reports after it is not seen here; see the operations guide.

    Raises:
        Refused: ``conflict`` of type :data:`ARCHIVE_BUCKET_LOCKED`.
    """
    stored = await conn.fetchrow(_STORED_IN_BUCKET_SQL)
    counts = (
        (int(stored["checkpoints"]), "checkpoint of a live sandbox", "checkpoints of live sandboxes"),
        (int(stored["snapshots"]), "environment snapshot", "environment snapshots"),
    )
    total = sum(count for count, _, _ in counts)
    if total:
        held = " and ".join(_counted(*named) for named in counts if named[0])
        raise Refused(
            "conflict",
            ARCHIVE_BUCKET_LOCKED,
            f"The archive bucket cannot be changed: {held} "
            f"{'is' if total == 1 else 'are'} stored in {current.s3_uri} "
            "and can be read back only from that bucket. "
            "Moving a deployment to another bucket is not supported.",
        )
    if not requested.enabled or requested.bucket == SANDBOX_ARCHIVE_BUCKET:
        return
    rows = await conn.fetch(
        _HOSTS_WITHOUT_DESTINATION_SQL, HOST_DISABLED, SANDBOX_ARCHIVE_DESTINATION_CAPABILITY
    )
    if rows:
        hosts = [row["host"] for row in rows]
        one = len(hosts) == 1
        raise Refused(
            "conflict",
            ARCHIVE_BUCKET_LOCKED,
            "The archive bucket cannot be changed while archives are enabled: "
            f"{_named(hosts)} cannot archive to another bucket, so "
            f"{'it' if one else 'they'} would take no work while "
            f"{'its sandbox manager' if one else 'their sandbox managers'} "
            f"kept archiving into the previous one. Redeploy {'it' if one else 'them'} first.",
        )


class SettingsMixin(HostPoolBase):
    """Read and change host-wide and archive settings."""

    async def host_settings(self) -> dict[str, Any]:
        """The runtime settings set for every server, as stored.

        A server's own values win over these, and a setting set nowhere falls
        back to the built-in default. Read through
        ``cloud_agent_shared.host_settings.admin_values``, which drops anything
        a release can no longer apply.
        """
        async with self._pool.acquire() as conn:
            stored = await conn.fetchval("SELECT host_settings FROM agent_runner_policy WHERE id")
        return dict(load_json(stored) or {})

    async def set_host_settings(self, *, changes: Mapping[str, int | None]) -> dict[str, Any]:
        """Set or clear runtime settings for every server.

        ``changes`` names only the settings to change, and ``None`` clears one.
        Every host applies the result from its next heartbeat answer, except
        where a value set for that server wins. The policy row is locked for
        the read-modify-write, the same first lock every other policy writer
        takes, so this serializes with them and with a concurrent change to
        another setting. Returns the values before the change, for the audit
        record.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            stored = await conn.fetchval(
                "SELECT host_settings FROM agent_runner_policy WHERE id FOR UPDATE"
            )
            previous = dict(load_json(stored) or {})
            # Upsert for the same reason the pin and the version selection are
            # one: against a database whose singleton row is missing, a bare
            # UPDATE touches nothing and still reports success.
            await conn.execute(
                """
                INSERT INTO agent_runner_policy (id, host_settings, host_settings_import_excluded)
                VALUES (TRUE, $1::jsonb, $2::text[])
                ON CONFLICT (id) DO UPDATE
                SET host_settings = EXCLUDED.host_settings,
                    host_settings_import_excluded = ARRAY(
                        SELECT DISTINCT key FROM unnest(
                            agent_runner_policy.host_settings_import_excluded ||
                            EXCLUDED.host_settings_import_excluded
                        ) AS touched(key)
                    ),
                    updated_at = NOW()
                """,
                json.dumps(_changed_settings(previous, changes)),
                list(changes),
            )
        return previous

    async def sandbox_archive_settings(self) -> dict[str, Any]:
        """Return the normalized deployment-wide archive policy and its timestamp."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT sandbox_archive_settings, sandbox_archive_revision,
                       sandbox_archive_updated_at
                FROM agent_runner_policy WHERE id
                """
            )
        return archive_settings(row)

    async def sandbox_archive_policy(self) -> SandboxArchivePolicy:
        """The stored archive policy, its access key opened.

        Raises:
            SandboxArchivePolicyError: The stored policy does not parse, or its
                key does not open.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT sandbox_archive_settings, sandbox_archive_revision
                FROM agent_runner_policy WHERE id
                """
            )
        return stored_archive_policy(row)

    async def set_sandbox_archive_settings(
        self,
        *,
        expected_revision: int,
        settings: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Atomically replace the archive policy, or return ``None`` on a stale revision.

        A bucket change is refused while the current bucket holds what is read
        back from it, or while an enabled policy in another bucket would not
        reach every host (see :func:`_refuse_bucket_change`).

        Raises:
            Refused: The bucket change is refused.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO agent_runner_policy (id) VALUES (TRUE) ON CONFLICT (id) DO NOTHING"
            )
            current = await conn.fetchrow(
                """
                SELECT sandbox_archive_settings, sandbox_archive_revision,
                       sandbox_archive_updated_at
                FROM agent_runner_policy WHERE id FOR UPDATE
                """
            )
            assert current is not None
            current_policy = stored_archive_policy(current)
            requested = SandboxArchivePolicy.from_mapping(
                {**dict(settings), "revision": current_policy.revision}
            )
            current_response = archive_settings_payload(
                current_policy,
                current["sandbox_archive_updated_at"],
                _changed=False,
            )
            if expected_revision > current_policy.revision:
                return None
            if requested.as_storage_dict() == current_policy.as_storage_dict():
                # Covers both an ordinary no-op and a retry after a successful
                # response was lost. Neither invents a new rollout revision.
                return current_response
            if current_policy.revision != expected_revision:
                return None
            if requested.bucket != current_policy.bucket:
                await _refuse_bucket_change(conn, current=current_policy, requested=requested)
            policy = SandboxArchivePolicy.from_mapping(
                {**dict(settings), "revision": current_policy.revision + 1}
            )
            updated_at = await conn.fetchval(
                """
                UPDATE agent_runner_policy
                SET sandbox_archive_settings = $1::jsonb,
                    sandbox_archive_revision = $2,
                    sandbox_archive_updated_at = NOW(),
                    updated_at = NOW()
                WHERE id
                RETURNING sandbox_archive_updated_at
                """,
                json.dumps(seal_archive_storage(policy.as_storage_dict())),
                policy.revision,
            )
        return archive_settings_payload(policy, updated_at, _changed=True)
