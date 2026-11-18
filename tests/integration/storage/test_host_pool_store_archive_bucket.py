"""The archive policy keeps another bucket in PostgreSQL, stores no default, and sets it once.

The bucket can change only while nothing read back from it is stored there,
and, for an enabled policy in another bucket, while every host can archive
there (#1014).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from tests.integration.storage.test_admin_sandbox_queries import seed
from tests.integration.storage.test_host_pool_store import (
    _archive_settings,
    db as db,
    hosts as hosts,
    store as store,
)

from cloud_agent.domain.errors import Refused
from cloud_agent_shared.sandbox_archive_settings import (
    SANDBOX_ARCHIVE_DESTINATION_CAPABILITY,
    SANDBOX_ARCHIVE_S3_URI,
)

if TYPE_CHECKING:
    from typing import Any

    from cloud_agent.storage.repositories.host_pool import PostgresHostPoolRepository

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]

_OTHER = "s3://example-archives/cloud-agent/sandbox"
_IMAGE = "ghcr.io/example/cloud-agent-sandbox@sha256:" + "a" * 64


async def test_archive_policy_keeps_another_bucket_and_stores_no_default(
    db, hosts: PostgresHostPoolRepository
):
    """A configured bucket survives the round trip; the default is never stored.

    So a row saved before the bucket was a setting reads the same as one saved
    after it.
    """
    uri = "s3://example-archives/cloud-agent/sandbox"
    changed = await hosts.set_sandbox_archive_settings(
        expected_revision=0, settings=_archive_settings(s3_uri=uri)
    )
    assert changed is not None and changed["s3_uri"] == uri
    assert (await hosts.sandbox_archive_settings())["s3_uri"] == uri

    back = await hosts.set_sandbox_archive_settings(
        expected_revision=1, settings=_archive_settings(s3_uri=SANDBOX_ARCHIVE_S3_URI)
    )
    assert back is not None and back["revision"] == 2
    assert back["s3_uri"] == SANDBOX_ARCHIVE_S3_URI
    async with db.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT sandbox_archive_settings ? 's3_uri' FROM agent_runner_policy WHERE id"
        )
    assert stored is False


async def _save(
    hosts: PostgresHostPoolRepository, revision: int, **changes: Any
) -> dict[str, Any] | None:
    return await hosts.set_sandbox_archive_settings(
        expected_revision=revision, settings=_archive_settings(**changes)
    )


async def _refused(hosts: PostgresHostPoolRepository, revision: int, **changes: Any) -> str:
    """The message of a refused bucket change, which leaves the policy as it was."""
    before = await hosts.sandbox_archive_settings()
    with pytest.raises(Refused) as refused:
        await _save(hosts, revision, **changes)
    assert (refused.value.kind, refused.value.type) == ("conflict", "archive_bucket_locked")
    assert await hosts.sandbox_archive_settings() == before
    return refused.value.message


async def test_the_bucket_stays_once_a_live_sandbox_has_a_checkpoint(db, store, hosts):
    """Restores read the bucket in force now, and a checkpoint row names none.

    So a change would leave the group to fail its restores and be lost. Other
    edits still save, and a group that can no longer be restored holds nothing.
    """
    assert await _save(hosts, 0) is not None
    await seed(db, store, "kept", state="stopped", host="runner-a")
    async with db.acquire() as conn:
        await conn.execute(
            """INSERT INTO agent_sandbox_checkpoints
               (id, sandbox_id, generation, host, container_id, base_image, runtime_id,
                object_key, sha256, size_bytes, new_layer_bytes, layer_count,
                format_version, captured_at, start_seq)
               VALUES ($1, 'kept', 1, 'runner-a', 'container-kept', $2, 'claude-code',
                       'cloud-agent/sandbox/v2/kept.oci.tar', $3, 4096, 2048, 3, 1, NOW(), 0)""",
            "c" * 32,
            _IMAGE,
            "e" * 64,
        )

    message = await _refused(hosts, 1, s3_uri=_OTHER)
    assert f"1 checkpoint of a live sandbox is stored in {SANDBOX_ARCHIVE_S3_URI}" in message
    assert "Moving a deployment to another bucket is not supported." in message
    # Disabling archives does not make the change safe: restores read it later.
    await _refused(hosts, 1, s3_uri=_OTHER, enabled=False)

    edited = await _save(hosts, 1, max_archive_bytes=1024**3)
    assert edited is not None and edited["revision"] == 2

    async with db.acquire() as conn:
        await conn.execute("UPDATE agent_sandboxes SET state = 'lost' WHERE id = 'kept'")
    moved = await _save(hosts, 2, s3_uri=_OTHER, max_archive_bytes=1024**3)
    assert moved is not None and moved["s3_uri"] == _OTHER


@pytest.mark.parametrize(
    ("state", "held"),
    [
        ("ready", True),
        ("superseded", True),
        ("building", True),
        ("queued", False),
        ("failed", False),
        ("deleting", False),
    ],
)
async def test_the_bucket_stays_while_an_environment_snapshot_is_stored(
    db, hosts, state: str, held: bool
):
    """A snapshot is loaded from the bucket in force now; one being built uploads to it."""
    assert await _save(hosts, 0) is not None
    async with db.acquire() as conn:
        await conn.execute(
            "INSERT INTO agent_environment_snapshots (id, user_id, environment_id, revision, "
            "setup_fingerprint, state, base_image, source_sha, object_key, sha256, size_bytes, "
            "ready_at) VALUES ($1, 'u1', 'env-1', 1, $2, $3, $4, $5, $6, $7, 1024, NOW())",
            "d" * 32,
            "f" * 64,
            state,
            _IMAGE,
            "b" * 40,
            "cloud-agent/sandbox/env/snapshot.oci.tar",
            "e" * 64,
        )

    if held:
        assert "1 environment snapshot is stored in" in await _refused(hosts, 1, s3_uri=_OTHER)
    else:
        moved = await _save(hosts, 1, s3_uri=_OTHER)
        assert moved is not None and moved["s3_uri"] == _OTHER


async def test_an_enabled_bucket_change_waits_for_every_host_to_archive_there(db, hosts):
    """An older host is sent no enabled policy in another bucket, and keeps archiving.

    A disabled policy still reaches it, in the default bucket, and so does the
    default; a removed host is not counted.
    """
    assert await _save(hosts, 0) is not None
    await hosts.register_host(
        host="runner-old", credential_hash="old-hash", capabilities=["shared-sandbox-v1"]
    )
    await hosts.register_host(
        host="runner-new",
        credential_hash="new-hash",
        capabilities=["shared-sandbox-v1", SANDBOX_ARCHIVE_DESTINATION_CAPABILITY],
    )

    message = await _refused(hosts, 1, s3_uri=_OTHER)
    assert "runner-old cannot archive to another bucket" in message
    assert "runner-new" not in message

    disabled = await _save(hosts, 1, s3_uri=_OTHER, enabled=False)
    assert disabled is not None and disabled["revision"] == 2
    back = await _save(hosts, 2, s3_uri=SANDBOX_ARCHIVE_S3_URI)
    assert back is not None and back["revision"] == 3

    async with db.acquire() as conn:
        await conn.execute(
            "UPDATE agent_runner_hosts SET status = 'disabled' WHERE host = 'runner-old'"
        )
    moved = await _save(hosts, 3, s3_uri=_OTHER)
    assert moved is not None and moved["s3_uri"] == _OTHER
