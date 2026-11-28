"""A CloudLab acquisition whose experiment fails without nodes tries the next node type.

The real reconciler and CloudLab adapter run against an in-memory store and a
mocked Portal API, so the assertions are on the requests CloudLab would see:
each experiment is created once, deleted before the next is created, and the
next one asks for a type not yet tried. No test contacts CloudLab.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import httpx
import pytest

from cloud_agent.application.cloudlab_inventory import CloudLabInventoryUnavailable
from cloud_agent.application.compute_reconcile import ComputeReconciler
from cloud_agent.domain.compute import public_operation
from cloud_agent.ports.errors import ConflictCode, StorageConflict
from cloud_agent.ports.external.compute_provider import (
    AcquireRequest,
    OperationIntent,
    ProviderError,
)
from cloud_agent.providers.compute.cloudlab import CloudLabProvider
from cloud_agent.schemas.compute import RenewalPolicy

NOW = datetime(2026, 11, 28, 12, tzinfo=UTC)
PREFERRED = ["r6615", "c6620", "r650"]
CONFIG = {
    "credential_ref": "account-1",
    "project": "research",
    "profile_name": "cloud-agent-node",
    "profile_project": "research",
    "node_types": PREFERRED,
}
PROFILES = {"profiles": [{"name": "cloud-agent-node", "project": "research"}]}
FAILED_EMPTY = {"urn:clemson": {"status": "failed", "nodes": []}}
FAILED_WITH_NODE = {
    "urn:clemson": {
        "status": "failed",
        "nodes": [{"urn": "urn:node:1", "client_id": "node-0", "hostname": "clnode1"}],
    }
}


class FakeCloudLab:
    """The Portal API calls an acquisition makes, with scripted experiment outcomes."""

    def __init__(self, outcomes: dict[str, dict[str, object]]) -> None:
        # node type -> GET body fields after creation ("status", "aggregates").
        self.outcomes = outcomes
        self.experiments: dict[str, dict[str, object]] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/v1")
        self.calls.append((request.method, path))
        if request.method == "GET" and path.startswith("/profiles"):
            return httpx.Response(200, json=PROFILES)
        if request.method == "POST":
            body = json.loads(request.content)
            node_type = body["bindings"]["phystype"]
            experiment = str(uuid4())
            self.experiments[experiment] = {
                "node_type": node_type,
                "name": body["name"],
                "deleted": False,
            }
            return httpx.Response(201, json={"id": experiment})
        experiment = path.removeprefix("/experiments/")
        record = self.experiments[experiment]
        if request.method == "DELETE":
            record["deleted"] = True
            return httpx.Response(204)
        if record["deleted"]:
            return httpx.Response(404, json={"error": "No such experiment"})
        # The first read follows the create at once; the outcome comes later.
        record["reads"] = int(record.get("reads", 0)) + 1
        outcome = {"status": "provisioning"}
        if record["reads"] > 1:
            outcome = self.outcomes.get(str(record["node_type"]), outcome)
        return httpx.Response(
            200, json={"id": experiment, "project": "research", "aggregates": {}, **outcome}
        )

    def posted(self) -> list[str]:
        return [str(record["node_type"]) for record in self.experiments.values()]


def harness(cloudlab: FakeCloudLab, *, inventory: object = None) -> SimpleNamespace:
    operation = {
        "id": "operation-a",
        "allocation_id": "allocation-a",
        "kind": "acquire",
        "request_id": "request-a",
        "fingerprint": "f" * 64,
        "state": "running",
        "step": "pending",
        "created_at": NOW,
        "worker_id": "worker-a",
        "generation": 1,
        "result": {},
        "parameters": {},
        "requested_by": "admin:a",
    }
    allocation = {
        "id": "allocation-a",
        "provider_id": "cloudlab",
        "mode": "provision",
        "management_mode": "manage",
        "external_id": None,
        "configuration": dict(CONFIG),
        "deployment_profile": {},
        "desired_state": "present",
        "observed_state": "unknown",
        "policy": RenewalPolicy().model_dump(),
        "nodes": [],
    }

    async def observe(**kwargs):
        observation = kwargs["observation"]
        if allocation["external_id"] not in (None, observation["external_id"]):
            raise AssertionError("an allocation's experiment identity never changes")
        allocation.update({k: v for k, v in observation.items() if k != "nodes"})
        if observation["observed_state"] != "terminated" and observation["nodes"]:
            allocation["nodes"] = [{"id": "node-row", **node} for node in observation["nodes"]]
        return allocation

    async def checkpoint(**kwargs):
        operation["step"] = kwargs["step"]
        operation["result"].update(kwargs.get("result") or {})
        return True

    async def finish(**kwargs):
        operation.update(state=kwargs["state"], step=kwargs["step"], message=kwargs["message"])
        operation["result"].update(kwargs.get("result") or {})
        return True

    async def reopen(**kwargs):
        if (
            allocation["external_id"] != kwargs["external_id"]
            or allocation["observed_state"] != "terminated"
            or allocation["nodes"]
        ):
            return False
        allocation.update(
            external_id=None, configuration=kwargs["configuration"], observed_state="unknown"
        )
        operation["step"] = "pending"
        operation["result"].update(kwargs["result"])
        return True

    repository = SimpleNamespace(
        get_allocation=AsyncMock(side_effect=lambda _id: allocation),
        observe_allocation=AsyncMock(side_effect=observe),
        checkpoint_operation=AsyncMock(side_effect=checkpoint),
        finish_operation=AsyncMock(side_effect=finish),
        reopen_failed_acquisition=AsyncMock(side_effect=reopen),
        assert_autoscale_acquire_connections_verified=AsyncMock(),
        record_observation_error=AsyncMock(),
        set_admission_hold=AsyncMock(),
        set_move_out_hold=AsyncMock(return_value=False),
    )
    resolver = AsyncMock()
    resolver.resolve.return_value = "account-token"
    provider = CloudLabProvider(
        api_url="https://cloudlab-api.example.org/v1",
        resolver=resolver,
        client=httpx.AsyncClient(transport=httpx.MockTransport(cloudlab)),
    )
    commands = SimpleNamespace(
        repository=repository,
        registry=SimpleNamespace(get=Mock(return_value=SimpleNamespace(provider=provider))),
        configuration=SimpleNamespace(poll_seconds=30, observation_seconds=120),
        cloudlab_inventory=inventory,
    )
    reconciler = ComputeReconciler(
        commands=commands, host_pool=SimpleNamespace(), bootstrap=SimpleNamespace()
    )
    return SimpleNamespace(
        operation=operation, allocation=allocation, repository=repository, reconciler=reconciler
    )


async def run(h: SimpleNamespace, times: int = 1) -> None:
    """Claim and execute the operation, as the worker does after each wait."""
    for _ in range(times):
        h.operation["state"] = "running"
        await h.reconciler.execute(h.operation)


def inventory(available: dict[str, int]) -> SimpleNamespace:
    nodes = [{"node_type": key, "available": value} for key, value in available.items()]
    return SimpleNamespace(preferred_nodes=AsyncMock(return_value={"nodes": nodes}))


@pytest.mark.asyncio
async def test_an_experiment_that_fails_without_nodes_is_deleted_then_the_next_type_tried():
    cloudlab = FakeCloudLab({"r6615": {"status": "failed", "aggregates": FAILED_EMPTY}})
    h = harness(cloudlab)

    await run(h)  # r6615 is accepted and still being created.
    assert h.operation["state"] == "waiting"
    assert h.operation["message"] == "Waiting for the provider to make the resource ready (r6615)."
    await run(h)  # It failed without nodes: deleted, and the acquisition reopened.
    assert h.operation["state"] == "waiting"
    assert h.operation["step"] == "pending"
    assert h.operation["message"].startswith("r6615 failed without nodes and was released")
    assert "c6620, r650 remain" in h.operation["message"]
    assert h.allocation["external_id"] is None
    assert h.allocation["configuration"] == CONFIG
    assert h.operation["result"]["provider_diagnostic"]["requested_hardware"] == "r6615"
    await run(h)  # c6620 is submitted as a new experiment.

    first, second = cloudlab.experiments
    assert cloudlab.posted() == ["r6615", "c6620"]
    # The failed experiment was deleted before the next one was created.
    assert [call for call in cloudlab.calls if call[0] in {"POST", "DELETE"}] == [
        ("POST", "/experiments/"),
        ("DELETE", f"/experiments/{first}"),
        ("POST", "/experiments/"),
    ]
    assert cloudlab.experiments[first]["name"] != cloudlab.experiments[second]["name"]
    assert h.allocation["external_id"] == second
    assert h.operation["state"] == "waiting"
    assert h.operation["message"] == (
        "r6615 could not be allocated; trying c6620. "
        "Waiting for the provider to make the resource ready."
    )
    assert public_operation(h.operation)["result"]["node_types_tried"] == ["r6615", "c6620"]


@pytest.mark.asyncio
async def test_each_experiment_is_created_once_however_often_the_operation_runs():
    cloudlab = FakeCloudLab({"r6615": {"status": "failed", "aggregates": FAILED_EMPTY}})
    h = harness(cloudlab)

    await run(h, times=8)

    assert cloudlab.posted() == ["r6615", "c6620"]
    assert sum(1 for method, _ in cloudlab.calls if method == "POST") == 2
    assert sum(1 for method, _ in cloudlab.calls if method == "DELETE") == 1
    assert h.operation["state"] == "waiting"


@pytest.mark.asyncio
async def test_an_experiment_that_failed_after_getting_nodes_is_not_retried_on_another_type():
    cloudlab = FakeCloudLab({"r6615": {"status": "failed", "aggregates": FAILED_WITH_NODE}})
    h = harness(cloudlab)

    await run(h, times=4)

    assert cloudlab.posted() == ["r6615"]
    assert h.operation["state"] == "failed"
    assert h.operation["step"] == "failed_resource_released"
    assert h.operation["result"]["blocked_reasons"][0]["code"] == "resource_failed"
    assert "Node types tried: r6615" in h.operation["message"]
    h.repository.reopen_failed_acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_every_type_failing_ends_the_acquisition_naming_each_one_tried():
    failed = {"status": "failed", "aggregates": FAILED_EMPTY}
    cloudlab = FakeCloudLab(dict.fromkeys(PREFERRED, failed))
    h = harness(cloudlab)

    await run(h, times=12)

    assert cloudlab.posted() == PREFERRED
    assert all(record["deleted"] for record in cloudlab.experiments.values())
    assert h.operation["state"] == "failed"
    assert h.operation["result"]["blocked_reasons"][0]["code"] == "resource_failed"
    assert h.operation["message"] == (
        "No preferred node type could be set up; each failed resource was released. "
        "Node types tried: r6615, c6620, r650."
    )
    # Nothing is retried after the end: the worker would not run it again.
    await run(h, times=2)
    assert cloudlab.posted() == PREFERRED


@pytest.mark.asyncio
async def test_a_released_failure_is_settled_without_reading_the_deleted_experiment():
    """A crash after the deletion, or a retry of an old failure, never asks CloudLab again."""
    cloudlab = FakeCloudLab({"r6615": {"status": "failed", "aggregates": FAILED_EMPTY}})
    h = harness(cloudlab)
    reopen = h.repository.reopen_failed_acquisition.side_effect
    h.repository.reopen_failed_acquisition.side_effect = StorageConflict(
        ConflictCode.OPERATION_LEASE_LOST
    )
    await run(h, times=2)  # The lease is lost after the deletion, before the reopen.
    assert h.operation["step"] == "failed_resource_released"
    assert h.allocation["observed_state"] == "terminated"
    calls = len(cloudlab.calls)
    h.repository.reopen_failed_acquisition.side_effect = reopen

    await run(h)

    assert cloudlab.calls[calls:] == []  # The deleted experiment is not read again.
    assert h.operation["step"] == "pending"
    await run(h)
    assert cloudlab.posted() == ["r6615", "c6620"]


@pytest.mark.asyncio
async def test_a_pinned_rotation_candidate_type_never_falls_back():
    cloudlab = FakeCloudLab({"r6615": {"status": "failed", "aggregates": FAILED_EMPTY}})
    h = harness(cloudlab)
    h.allocation["configuration"] = {**CONFIG, "bindings": {"phystype": "r6615"}}

    await run(h, times=4)

    assert cloudlab.posted() == ["r6615"]
    assert h.operation["state"] == "failed"
    assert "node_types_tried" not in h.operation["result"]
    h.repository.reopen_failed_acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_node_moves_replacement_leaves_the_next_type_to_the_move():
    """Even when the replacement's configuration leaves CloudLab a choice of types."""
    cloudlab = FakeCloudLab({"r6615": {"status": "failed", "aggregates": FAILED_EMPTY}})
    h = harness(cloudlab)
    h.operation["parameters"] = {"rotation_operation_id": "rotate-a", "node_type": "r6615"}

    await run(h, times=4)

    assert cloudlab.posted() == ["r6615"]
    assert h.operation["state"] == "failed"
    assert h.operation["result"]["blocked_reasons"][0]["code"] == "resource_failed"
    h.repository.reopen_failed_acquisition.assert_not_awaited()


@pytest.mark.asyncio
async def test_types_the_inventory_shows_fully_used_are_tried_last():
    cloudlab = FakeCloudLab({})
    h = harness(cloudlab, inventory=inventory({"r6615": 0, "c6620": 0, "r650": 3}))

    await run(h)

    assert cloudlab.posted() == ["r650"]
    assert h.operation["result"]["node_types_untried"] == ["r6615", "c6620"]


@pytest.mark.asyncio
async def test_an_unreadable_inventory_keeps_the_configured_order():
    cloudlab = FakeCloudLab({})
    unreadable = SimpleNamespace(
        preferred_nodes=AsyncMock(side_effect=CloudLabInventoryUnavailable)
    )
    h = harness(cloudlab, inventory=unreadable)

    await run(h)

    unreadable.preferred_nodes.assert_awaited_once()
    assert cloudlab.posted() == ["r6615"]
    assert h.operation["result"]["node_types_untried"] == ["c6620", "r650"]


@pytest.mark.asyncio
async def test_a_fallback_skips_tried_types_and_still_prefers_available_ones():
    failed = {"status": "failed", "aggregates": FAILED_EMPTY}
    cloudlab = FakeCloudLab({"r6615": failed})
    h = harness(cloudlab, inventory=inventory({"c6620": 0}))

    await run(h, times=3)

    assert cloudlab.posted() == ["r6615", "r650"]
    assert h.operation["result"]["node_types_untried"] == ["c6620"]


def _intent() -> OperationIntent:
    return OperationIntent(
        operation_id="operation-a",
        allocation_id="0f3b8c4e-0000-4000-8000-000000000000",
        requested_at=NOW,
        request_fingerprint="f" * 64,
    )


@pytest.mark.asyncio
async def test_the_adapter_reports_what_it_tried_and_what_is_left():
    def respond(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json=PROFILES)
        node_type = json.loads(request.content)["bindings"]["phystype"]
        if node_type == "c6620":
            return httpx.Response(429, json={"error": "Not enough free nodes of type c6620"})
        return httpx.Response(201, json={"id": str(uuid4())})

    adapter = CloudLabProvider(
        api_url="https://cloudlab-api.example.org/v1",
        resolver=AsyncMock(resolve=AsyncMock(return_value="account-token")),
        client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    config = {**CONFIG, "node_types": ["r6615", "c6620", "r650", "r6525"]}
    receipt = await adapter.acquire(
        AcquireRequest(
            mode="provision",
            config=config,
            tried_node_types=("r6615",),
            unavailable_node_types=("r650",),
        ),
        _intent(),
    )

    # r6615 was tried before; r650 has nothing free, so it moves behind r6525.
    assert receipt.node_types_tried == ("c6620", "r6525")
    assert receipt.node_types_untried == ("r650",)
    assert receipt.resource.config["bindings"] == {"phystype": "r6525"}
    assert receipt.resource.config["name"] == "ca-0f3b8c4e000-1"

    with pytest.raises(ProviderError) as exhausted:
        await adapter.acquire(
            AcquireRequest(
                mode="provision", config=config, tried_node_types=tuple(config["node_types"])
            ),
            _intent(),
        )
    assert exhausted.value.code == "capacity_unavailable"
