"""The deployment's drain command: the admin API's fence, without ``ADMIN_TOKEN``."""

from __future__ import annotations

import io
import json
from typing import Any
from uuid import UUID, uuid4

import pytest
from tests.unit.test_host_pool_api import (  # noqa: F401 - shared pytest fixtures
    ADMIN,
    FakeHostPoolStore,
    client,
)

from cloud_agent.deployment import host_fence
from cloud_agent.deployment.host_fence import ACTOR, FenceRefused, run
from cloud_agent.domain.host_pool import HOST_DRAINING, HOST_ONLINE


class RecordingAudit:
    """The audit capability, keeping every row it was asked to write."""

    def __init__(self, *, fail: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.fail = fail

    async def record_admin_action(self, **row: Any) -> int | None:
        if self.fail:
            raise ConnectionError("audit table unavailable")
        self.rows.append(row)
        return len(self.rows)


def _acquire(store: FakeHostPoolStore, token: UUID, *, epoch: UUID | None = None) -> str:
    return json.dumps(
        {
            "expected_fence_token": None,
            "expected_fence_epoch": str(epoch or store.rows["runner-a"]["drain_fence_epoch"]),
            "fence_token": str(token),
        }
    )


# Volatile per read: the read's own instant, and the ages measured from it.
_VOLATILE = {"observed_at", "seconds_since_seen"}


def _stable(document: Any) -> Any:
    if isinstance(document, dict):
        return {key: _stable(value) for key, value in document.items() if key not in _VOLATILE}
    if isinstance(document, list):
        return [_stable(value) for value in document]
    return document


async def test_the_snapshot_is_the_document_the_admin_api_answers(client):  # noqa: F811
    async_client, store = client
    store.seed("runner-a", status=HOST_ONLINE)
    store.seed("runner-b", status=HOST_DRAINING)

    api = await async_client.get("/admin/agent/runner-hosts", headers=ADMIN)
    command = await run("snapshot", None, "", host_pool=store, audit=RecordingAudit())

    assert api.status_code == 200
    assert _stable(command) == _stable(api.json())


async def test_drain_acquires_the_fence_and_is_audited_as_the_deployment():
    store, audit, token = FakeHostPoolStore(), RecordingAudit(), uuid4()
    store.seed("runner-a", status=HOST_ONLINE)

    document = await run("drain", "runner-a", _acquire(store, token), host_pool=store, audit=audit)

    row = store.rows["runner-a"]
    assert (row["status"], row["drain_fence_token"], row["drain_fence_active"]) == (
        HOST_DRAINING,
        token,
        True,
    )
    (returned,) = document["hosts"]
    assert returned["drain_fence_token"] == str(token)
    assert returned["drain_fence_epoch"] == str(row["drain_fence_epoch"])
    assert audit.rows == [
        {
            "action": "agent.runner_host.drain",
            "actor_id": ACTOR,
            "admin_ip": "local",
            "details": {"host": "runner-a"},
        }
    ]
    # An automation, as migration 0114 classifies it: never break-glass access.
    assert ACTOR.startswith("service:")


async def test_undrain_releases_only_this_deployments_fence():
    store, audit, token = FakeHostPoolStore(), RecordingAudit(), uuid4()
    store.seed("runner-a", status=HOST_ONLINE)
    await run("drain", "runner-a", _acquire(store, token), host_pool=store, audit=audit)

    with pytest.raises(FenceRefused) as refused:
        await run(
            "undrain",
            "runner-a",
            json.dumps({"fence_token": str(uuid4())}),
            host_pool=store,
            audit=audit,
        )
    assert refused.value.status == 409
    assert store.rows["runner-a"]["status"] == HOST_DRAINING

    await run(
        "undrain",
        "runner-a",
        json.dumps({"fence_token": str(token)}),
        host_pool=store,
        audit=audit,
    )
    assert store.rows["runner-a"]["status"] == HOST_ONLINE
    assert [row["action"] for row in audit.rows] == [
        "agent.runner_host.drain",
        "agent.runner_host.undrain",
    ]


async def test_a_stale_epoch_is_refused_as_the_api_refuses_it_and_records_nothing():
    store, audit, token = FakeHostPoolStore(), RecordingAudit(), uuid4()
    store.seed("runner-a", status=HOST_ONLINE)

    with pytest.raises(FenceRefused) as refused:
        await run(
            "drain", "runner-a", _acquire(store, token, epoch=uuid4()), host_pool=store, audit=audit
        )

    assert refused.value.status == 409
    assert str(token) not in refused.value.detail
    assert store.rows["runner-a"]["status"] == HOST_ONLINE
    assert audit.rows == []


async def test_an_unknown_host_is_refused_with_404():
    store = FakeHostPoolStore()
    with pytest.raises(FenceRefused) as refused:
        await run(
            "undrain",
            "nowhere",
            json.dumps({"fence_token": str(uuid4())}),
            host_pool=store,
            audit=RecordingAudit(),
        )
    assert refused.value.status == 404


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        json.dumps({"fence_token": "not-a-uuid-but-secret"}),
        json.dumps({"expected_fence_token": None, "fence_token": "secret-ish"}),
    ],
)
async def test_an_invalid_body_names_fields_never_values(body: str):
    store = FakeHostPoolStore()
    store.seed("runner-a", status=HOST_ONLINE)

    with pytest.raises(FenceRefused) as refused:
        await run("drain", "runner-a", body, host_pool=store, audit=RecordingAudit())

    assert refused.value.status == 422
    assert "secret" not in refused.value.detail
    assert "not-a-uuid" not in refused.value.detail
    assert store.rows["runner-a"]["status"] == HOST_ONLINE


async def test_an_oversized_body_is_refused_before_parsing():
    store = FakeHostPoolStore()
    store.seed("runner-a", status=HOST_ONLINE)
    with pytest.raises(FenceRefused) as refused:
        await run(
            "drain",
            "runner-a",
            " " * (host_fence.MAX_INPUT_BYTES + 1),
            host_pool=store,
            audit=RecordingAudit(),
        )
    assert refused.value.status == 422


async def test_a_failed_audit_write_does_not_undo_or_fail_the_drain(capsys):
    store, token = FakeHostPoolStore(), uuid4()
    store.seed("runner-a", status=HOST_ONLINE)

    await run(
        "drain",
        "runner-a",
        _acquire(store, token),
        host_pool=store,
        audit=RecordingAudit(fail=True),
    )

    assert store.rows["runner-a"]["status"] == HOST_DRAINING
    assert "audit row for agent.runner_host.drain was not written" in capsys.readouterr().err


def test_main_reads_tokens_from_stdin_and_prints_the_pool(monkeypatch, capsys):
    seen: list[tuple[str, str | None, str]] = []

    async def connected(command: str, host: str | None, body: str) -> dict[str, Any]:
        seen.append((command, host, body))
        return {"hosts": []}

    monkeypatch.setattr(host_fence, "_connected", connected)
    monkeypatch.setattr("sys.stdin", io.StringIO('{"fence_token": "x"}'))

    assert host_fence.main(["undrain", "runner-a"]) == 0
    assert seen == [("undrain", "runner-a", '{"fence_token": "x"}')]
    assert json.loads(capsys.readouterr().out) == {"hosts": []}


def test_main_does_not_read_stdin_for_a_snapshot(monkeypatch, capsys):
    seen: list[str] = []

    async def connected(command: str, host: str | None, body: str) -> dict[str, Any]:
        seen.append(body)
        return {"hosts": []}

    class Unreadable(io.StringIO):
        def read(self, *_args: Any) -> str:
            raise AssertionError("snapshot must not wait on standard input")

    monkeypatch.setattr(host_fence, "_connected", connected)
    monkeypatch.setattr("sys.stdin", Unreadable())

    assert host_fence.main(["snapshot"]) == 0
    assert seen == [""]


def test_main_reports_a_refusal_as_json_on_stderr_and_exits_2(monkeypatch, capsys):
    async def connected(*_args: Any) -> dict[str, Any]:
        raise FenceRefused(409, "ownership changed", code=None)

    monkeypatch.setattr(host_fence, "_connected", connected)
    monkeypatch.setattr("sys.stdin", io.StringIO("{}"))

    assert host_fence.main(["drain", "runner-a"]) == host_fence.REFUSED
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err) == {"status": 409, "code": None, "detail": "ownership changed"}
