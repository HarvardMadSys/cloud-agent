"""Reopening an acquisition whose experiment failed without nodes, in real transactions."""

from __future__ import annotations

from uuid import uuid4

import pytest
from tests.integration.storage.test_compute_resources import fence
from tests.integration.storage.test_host_pool_store import (
    db as db,
    store as store,
)

from cloud_agent.domain.host_pool import hash_secret
from cloud_agent.ports.errors import ConflictCode, StorageConflict

pytestmark = [pytest.mark.dbtest, pytest.mark.asyncio]

BASE = {"project": "research", "node_types": ["r6615", "c6620"]}


async def failed_acquisition(store, *, nodes: list[dict] | None = None):
    """An acquisition whose experiment failed and whose deletion was recorded."""
    repo = store.compute_resources
    request = str(uuid4())
    created = await repo.create_allocation_operation(
        request_id=request,
        fingerprint=request,
        kind="acquire",
        allocation={
            "name": "machine",
            "provider_id": "cloudlab",
            "mode": "provision",
            "management_mode": "manage",
            "configuration": BASE,
            "deployment_profile": {"id": "profile-" + request},
            "lease_managed": True,
            "release_supported": True,
        },
        parameters={},
        requested_by="admin",
    )
    op = await repo.claim_operation(worker_id="worker")
    assert op["id"] == created["id"]
    allocation_id = op["allocation_id"]
    experiment = str(uuid4())
    chosen = {**BASE, "bindings": {"phystype": "r6615"}, "external_id": experiment}
    for state in ("failed", "terminated"):
        await repo.observe_allocation(
            allocation_id=allocation_id,
            observation={
                "external_id": experiment,
                "configuration": chosen,
                "observed_state": state,
                "scope_complete": state == "terminated",
                "nodes": nodes or [],
            },
            **fence(op),
        )
    assert await repo.checkpoint_operation(
        **fence(op),
        step="failed_resource_released",
        result={"resource_ref": {"external_id": experiment}, "external_id": experiment},
    )
    return op, allocation_id, experiment


async def reopen(repo, op, allocation_id, experiment):
    return await repo.reopen_failed_acquisition(
        allocation_id=allocation_id,
        external_id=experiment,
        configuration=BASE,
        result={"resource_ref": None, "external_id": None, "external_pending": False},
        **fence(op),
    )


async def test_a_deleted_experiment_without_nodes_is_forgotten_with_the_step(store):
    repo = store.compute_resources
    op, allocation_id, experiment = await failed_acquisition(store)
    await repo.create_node_registration(
        allocation_id=allocation_id, token_hash=hash_secret("old"), ttl_seconds=3600, **fence(op)
    )

    assert await reopen(repo, op, allocation_id, experiment)

    allocation = await repo.get_allocation(allocation_id)
    assert allocation["external_id"] is None
    assert allocation["observed_state"] == "unknown"
    assert allocation["configuration"] == BASE
    assert allocation["nodes"] == []
    assert await repo.node_registration(allocation_id=allocation_id) is None
    saved = await repo.get_operation(op["id"])
    assert saved["step"] == "pending"
    assert saved["result"]["resource_ref"] is None and saved["result"]["external_id"] is None
    # The next experiment's identity is saved like the first one's.
    following = str(uuid4())
    await repo.observe_allocation(
        allocation_id=allocation_id,
        observation={"external_id": following, "observed_state": "pending", "nodes": []},
        **fence(op),
    )
    assert (await repo.get_allocation(allocation_id))["external_id"] == following


async def test_an_experiment_that_had_a_node_or_another_identity_is_never_forgotten(store):
    repo = store.compute_resources
    node = {"provider_node_id": "agg:node-0", "instance_identity": "i-1", "name": "node-0"}
    op, allocation_id, experiment = await failed_acquisition(store, nodes=[node])
    assert not await reopen(repo, op, allocation_id, experiment)
    assert (await repo.get_allocation(allocation_id))["external_id"] == experiment
    assert (await repo.get_operation(op["id"]))["step"] == "failed_resource_released"
    await repo.finish_operation(**fence(op), state="failed", step="failed_resource_released")

    op, allocation_id, experiment = await failed_acquisition(store)
    assert not await reopen(repo, op, allocation_id, str(uuid4()))
    assert (await repo.get_allocation(allocation_id))["external_id"] == experiment


async def test_only_the_acquisitions_current_lease_may_reopen_it(store):
    repo = store.compute_resources
    op, allocation_id, experiment = await failed_acquisition(store)
    stale = {**op, "generation": op["generation"] + 1}
    with pytest.raises(StorageConflict) as lost:
        await reopen(repo, stale, allocation_id, experiment)
    assert lost.value.code is ConflictCode.OPERATION_LEASE_LOST
    assert (await repo.get_allocation(allocation_id))["external_id"] == experiment
