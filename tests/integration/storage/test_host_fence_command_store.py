"""The deployment's drain command on the real fence and the real audit trail."""

from __future__ import annotations

# Imported pytest fixtures are intentionally named by test parameters.
# ruff: noqa: F811
import json
from uuid import uuid4

import pytest
from tests.integration.storage.test_host_pool_store import (
    HOST_DRAINING,
    HOST_ONLINE,
    PostgresHostPoolRepository,
    _drain_epoch,
    _enrol,
    db,  # noqa: F401 - pytest fixture
    hosts,  # noqa: F401 - pytest fixture
    store,  # noqa: F401 - pytest fixture
)

from cloud_agent.deployment.host_fence import FenceRefused, run

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]


async def test_a_deployment_drain_round_trip_is_fenced_and_audited_as_a_service(
    db, store, hosts: PostgresHostPoolRepository
):
    await _enrol(store, "runner-a", hosts=hosts)
    token = uuid4()
    acquire = json.dumps(
        {
            "expected_fence_token": None,
            "expected_fence_epoch": str(await _drain_epoch(hosts)),
            "fence_token": str(token),
        }
    )

    drained = await run("drain", "runner-a", acquire, host_pool=hosts, audit=store.audit)
    (row,) = drained["hosts"]
    assert (row["status"], row["drain_fence_token"], row["drain_fence_active"]) == (
        HOST_DRAINING,
        str(token),
        True,
    )

    # The same body again is the response-loss retry the helper relies on.
    again = await run("drain", "runner-a", acquire, host_pool=hosts, audit=store.audit)
    assert again["hosts"][0]["drain_fence_epoch"] == row["drain_fence_epoch"]

    # Another deployment's token cannot release this one's drain.
    with pytest.raises(FenceRefused) as foreign:
        await run(
            "undrain",
            "runner-a",
            json.dumps({"fence_token": str(uuid4())}),
            host_pool=hosts,
            audit=store.audit,
        )
    assert foreign.value.status == 409

    released = await run(
        "undrain",
        "runner-a",
        json.dumps({"fence_token": str(token)}),
        host_pool=hosts,
        audit=store.audit,
    )
    assert released["hosts"][0]["status"] == HOST_ONLINE

    async with db.acquire() as conn:
        audited = await conn.fetch(
            "SELECT action, actor_id, actor_type, details FROM agent_admin_audit_log "
            "WHERE action LIKE 'agent.runner_host.%' ORDER BY id"
        )
    assert [(r["action"], r["actor_id"], r["actor_type"]) for r in audited] == [
        ("agent.runner_host.drain", "service:deployment-drain", "service"),
        ("agent.runner_host.drain", "service:deployment-drain", "service"),
        ("agent.runner_host.undrain", "service:deployment-drain", "service"),
    ]
    assert all(str(token) not in str(r["details"]) for r in audited)
