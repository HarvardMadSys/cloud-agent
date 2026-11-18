"""A momentary conflict on a turn's first sandbox request is asked again, not queued.

A new chat's first message on staging met a 409 a second or two into its
sandbox request, and the turn went back to the queue: thirty seconds of retry
delay, a stop of the container it had just created, and a fresh start, before
"hello" was answered. The sandbox manager now names its conflicts, and the
runner asks again, briefly, for the ones that are over in a moment.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException
from tests.unit.test_agent_runner import (
    _configure_host,
    _enable_profiling,
    _profiled_measurement,
    _run_shared_boundary_case,
)
from tests.unit.test_agent_sandbox_manager import _app, _identity, _SharedSupervisor
from tests.unit.test_shared_session_supervisor import (
    SharedChatSessionSupervisor,
    _Manager,
    _members,
    _spec,
)

from cloud_agent_host.manager_api.context import supervisor_error
from cloud_agent_host.sandbox_manager_client import SandboxManagerClient, SandboxManagerError
from cloud_agent_host.session_lifecycle import (
    CONFLICT_HEADER,
    SessionBusy,
    SessionChanged,
    SessionLeaseLost,
    StaleStartFence,
)
from cloud_agent_host.turn import shared_live_attempt
from cloud_agent_shared import operation_profiling

# ── the sandbox manager names its conflicts ────────────────────────────────


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (SessionBusy("chat 'athr_a' already has an active turn"), "busy"),
        (SessionChanged("shared sandbox lifecycle changed during activation"), "changed"),
        (SessionLeaseLost("member activation generation is stale"), "lease_lost"),
        (StaleStartFence("start fence is sealed"), "lease_lost"),
    ],
)
def test_every_409_says_which_conflict_it_is(error, kind):
    refusal = supervisor_error(error)

    assert isinstance(refusal, HTTPException)
    assert refusal.status_code == 409
    assert refusal.detail == str(error)
    assert refusal.headers == {CONFLICT_HEADER: kind}


@pytest.mark.asyncio
async def test_the_acquire_route_answers_the_conflict_and_names_it_in_its_own_log(caplog):
    supervisor = _SharedSupervisor()

    def refuse(*_args, **_kwargs):
        raise SessionBusy("chat 'athr_source' already has an active turn")

    supervisor.acquire = refuse
    caplog.set_level(logging.WARNING, logger="cloud_agent_host.sandbox_manager")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(supervisor)), base_url="http://sandbox_manager"
    ) as client:
        answer = await client.post(
            "/shared-sandboxes/athr_source/acquire",
            headers={"X-Agent-Workspace-Token": "sandbox-manager-secret"},
            json={**_identity(), "owner": "attempt:1", "activation_generation": 4},
        )

    assert answer.status_code == 409
    assert answer.headers[CONFLICT_HEADER] == "busy"
    assert answer.json() == {"detail": "chat 'athr_source' already has an active turn"}
    logged = [r.getMessage() for r in caplog.records if "shared_acquire_conflict" in r.msg]
    assert logged == [
        "shared_acquire_conflict thread_id=athr_source owner=attempt:1 conflict=busy "
        "detail=chat 'athr_source' already has an active turn"
    ]


def test_a_changed_lifecycle_is_still_a_lost_lease_to_every_older_caller():
    """Callers that catch a lost lease keep catching this one."""
    assert issubclass(SessionChanged, SessionLeaseLost)


def _refusing_client(status: int, headers: dict[str, str]) -> SandboxManagerClient:
    def refuse(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, headers=headers, json={"detail": "refused"})

    client = SandboxManagerClient("http://sandbox_manager", "secret")
    client._client.close()
    client._client = httpx.Client(transport=httpx.MockTransport(refuse))
    return client


@pytest.mark.parametrize(
    ("status", "headers", "conflict"),
    [
        (409, {CONFLICT_HEADER: "busy"}, "busy"),
        (409, {CONFLICT_HEADER: "changed"}, "changed"),
        (409, {CONFLICT_HEADER: "lease_lost"}, "lease_lost"),
        # An older sandbox manager says nothing, and a value this runner does
        # not know is no more than that.
        (409, {}, None),
        (409, {CONFLICT_HEADER: "maybe-later"}, None),
        (503, {CONFLICT_HEADER: "busy"}, None),
    ],
)
def test_the_runner_reads_the_conflict_only_from_a_409(status, headers, conflict):
    client = _refusing_client(status, headers)
    try:
        with pytest.raises(SandboxManagerError) as caught:
            client.list()
    finally:
        client.close()

    assert caught.value.status_code == status
    assert caught.value.conflict == conflict
    assert str(caught.value) == "refused"


# ── the activation race is a changed lifecycle, not a lost lease ────────────


def _acquire(supervisor: SharedChatSessionSupervisor):
    return supervisor.acquire(
        _spec(),
        thread_id="athr_source",
        owner="attempt:1",
        activation_generation=1,
        has_settled_context=True,
        manifest=_members(),
    )


def test_a_group_that_moved_on_during_activation_can_be_asked_again(monkeypatch):
    """A lifecycle check that begins or ends while Docker answers changes nothing of the turn's."""
    manager = _Manager()
    supervisor = SharedChatSessionSupervisor(manager)
    supervisor.register_manifest(_spec(), _members())
    group = supervisor.state.members["athr_source"]
    inspect = manager.inspect

    def moved_on(container_name):
        with supervisor.state.lock:
            group.life.generation += 1
        return inspect(container_name)

    monkeypatch.setattr(manager, "inspect", moved_on)
    with pytest.raises(SessionChanged, match="lifecycle changed during activation"):
        _acquire(supervisor)

    monkeypatch.setattr(manager, "inspect", inspect)
    assert _acquire(supervisor).token


def test_a_member_replaced_during_activation_is_still_a_lost_lease(monkeypatch):
    manager = _Manager()
    supervisor = SharedChatSessionSupervisor(manager)
    supervisor.register_manifest(_spec(), _members())
    group = supervisor.state.members["athr_source"]
    inspect = manager.inspect

    def replaced(container_name):
        with supervisor.state.lock:
            group.members["athr_source"] = dataclasses.replace(group.members["athr_source"])
        return inspect(container_name)

    monkeypatch.setattr(manager, "inspect", replaced)
    with pytest.raises(SessionLeaseLost, match="lifecycle changed during activation") as caught:
        _acquire(supervisor)
    assert not isinstance(caught.value, SessionChanged)


# ── the runner asks again for a momentary conflict ─────────────────────────


def _fake_clock(monkeypatch, *, on_sleep=None) -> SimpleNamespace:
    """The runner's pauses advance a clock of their own instead of real time."""
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def sleep(seconds):
        clock.sleeps.append(seconds)
        clock.now += seconds
        if on_sleep is not None:
            on_sleep()

    monkeypatch.setattr(
        shared_live_attempt,
        "time",
        SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep),
    )
    return clock


def _refuse_first(*errors: SandboxManagerError, calls: list[dict] | None = None):
    """Make the harness's sandbox manager refuse ``errors`` in turn, then answer as before."""

    def configure(_control, supervisor, _finish):
        answer = supervisor.acquire_shared.side_effect
        refusals = iter(errors)

        def acquire(*args, **kwargs):
            if calls is not None:
                calls.append(kwargs)
            error = next(refusals, None)
            if error is not None:
                raise error
            return answer(*args, **kwargs)

        supervisor.acquire_shared.side_effect = acquire

    return configure


def _busy(conflict: str | None = "busy") -> SandboxManagerError:
    return SandboxManagerError(409, "chat 'athr_a' already has an active turn", conflict=conflict)


def test_a_turn_that_meets_a_momentary_conflict_runs_without_going_back_to_the_queue(
    monkeypatch, caplog
):
    clock = _fake_clock(monkeypatch)
    caplog.set_level(logging.WARNING, logger="cloud_agent_host.runner")

    result, control, supervisor, finish = _run_shared_boundary_case(
        monkeypatch,
        0,
        None,
        "claude-code",
        configure=_refuse_first(
            _busy(),
            SandboxManagerError(
                409, "shared sandbox lifecycle changed during activation", conflict="changed"
            ),
        ),
    )

    assert result == 0
    assert supervisor.acquire_shared.call_count == 3
    assert clock.sleeps[:2] == [0.1] * 2  # polled, for a Stop, in short steps
    assert sum(clock.sleeps) == pytest.approx(0.5 + 1.0)
    control.release_turn.assert_not_called()
    control.abandon_chat_sandbox.assert_not_called()
    control.admit_chat_turn.assert_called_once()
    assert finish.call_args.args[0] == "succeeded"
    logged = [r.getMessage() for r in caplog.records if "supervisor_acquire_conflict" in r.msg]
    assert len(logged) == 2
    assert "conflict=busy retrying=True elapsed_s=0.00" in logged[0]
    assert "conflict=changed retrying=True elapsed_s=0.50" in logged[1]
    # The runner's failure records leave the sandbox manager's words out.
    assert not any("active turn" in line or "lifecycle" in line for line in logged)


@pytest.mark.parametrize("conflict", ["lease_lost", None])
def test_a_stale_lease_or_an_unnamed_conflict_still_goes_back_at_once(
    monkeypatch, caplog, conflict
):
    """A newer activation owns the member, or an older sandbox manager did not say."""
    clock = _fake_clock(monkeypatch)
    caplog.set_level(logging.WARNING, logger="cloud_agent_host.runner")

    result, control, supervisor, finish = _run_shared_boundary_case(
        monkeypatch, 0, None, "claude-code", configure=_refuse_first(_busy(conflict))
    )

    assert result == 0
    supervisor.acquire_shared.assert_called_once()
    assert clock.sleeps == []
    control.release_turn.assert_called_once_with("sandbox_busy")
    control.admit_chat_turn.assert_not_called()
    finish.assert_not_called()
    logged = [r.getMessage() for r in caplog.records if "supervisor_acquire_conflict" in r.msg]
    assert len(logged) == 1
    assert logged[0].endswith(f"conflict={conflict or 'unknown'} retrying=False elapsed_s=0.00")


def test_a_conflict_that_outlasts_the_budget_goes_back_to_the_queue(monkeypatch):
    clock = _fake_clock(monkeypatch)

    result, control, supervisor, _finish = _run_shared_boundary_case(
        monkeypatch, 0, None, "claude-code", configure=_refuse_first(*[_busy()] * 20)
    )

    assert result == 0
    # 0.5 + 1 + 2 + 4 seconds of pauses; the next, 8, would pass the budget.
    assert supervisor.acquire_shared.call_count == 5
    assert sum(clock.sleeps) == pytest.approx(7.5)
    assert sum(clock.sleeps) <= shared_live_attempt.ACQUIRE_RETRY_BUDGET_S
    control.release_turn.assert_called_once_with("sandbox_busy")
    control.admit_chat_turn.assert_not_called()


def test_a_conflict_after_the_sandbox_managers_own_long_wait_is_not_asked_again(monkeypatch):
    """A snapshot capture can outlast the manager's 30-second wait; the budget is spent already."""
    clock = _fake_clock(monkeypatch)

    def configure(control, supervisor, finish):
        def waited_then_refused(*_args, **_kwargs):
            clock.now += 30.0
            raise _busy()

        supervisor.acquire_shared.side_effect = waited_then_refused

    result, control, supervisor, _finish = _run_shared_boundary_case(
        monkeypatch, 0, None, "claude-code", configure=configure
    )

    assert result == 0
    supervisor.acquire_shared.assert_called_once()
    assert clock.sleeps == []
    control.release_turn.assert_called_once_with("sandbox_busy")


@pytest.mark.parametrize("flag", ["cancel_requested", "lease_lost"])
def test_a_stop_or_a_lost_lease_ends_the_pause(monkeypatch, flag):
    heart = SimpleNamespace(cancel_requested=False, lease_lost=False)
    clock = _fake_clock(monkeypatch, on_sleep=lambda: setattr(heart, flag, True))

    result, control, supervisor, _finish = _run_shared_boundary_case(
        monkeypatch, 0, None, "claude-code", configure=_refuse_first(_busy(), _busy()), heart=heart
    )

    assert result == 0
    supervisor.acquire_shared.assert_called_once()
    assert clock.sleeps == [0.1]
    control.release_turn.assert_called_once_with("sandbox_busy")
    control.admit_chat_turn.assert_not_called()


def test_a_retried_acquire_still_completes_its_startup_measurement(monkeypatch, tmp_path):
    """Only the answered request carries the stage's fragment, and it answers for the stage."""
    _configure_host(monkeypatch)
    sink = tmp_path / "startup.jsonl"
    _enable_profiling(monkeypatch)
    monkeypatch.setenv(operation_profiling.ENV_PROFILING_SINK, str(sink))
    measurement = _profiled_measurement()
    assert measurement is not None
    _fake_clock(monkeypatch)
    calls: list[dict] = []
    refuse = _refuse_first(_busy(), calls=calls)

    def configure(control, supervisor, finish):
        refuse(control, supervisor, finish)

        def stream(**kwargs):
            operation_profiling.finish(kwargs.get("measurement"), outcome="dispatched")
            return 0, "", [], {"kind": "result", "status": "completed", "exit_code": 0}, None

        monkeypatch.setattr(shared_live_attempt, "_stream_shared_agent_turn", stream)

    result, _control, _supervisor, _finish = _run_shared_boundary_case(
        monkeypatch,
        code=0,
        frame={"kind": "result", "status": "completed", "exit_code": 0},
        runtime_id="claude-code",
        configure=configure,
        measurement=measurement,
        container_action="reuse",
        resident_action="reuse",
    )

    assert result == 0
    assert len(calls) == 2
    assert calls[0]["profiling"]["call_id"] == calls[1]["profiling"]["call_id"]
    operation_profiling.flush_sink()
    record = json.loads(sink.read_text().splitlines()[0])
    assert record["startup_outcome"] == "dispatched"
    assert record["measurement_complete"] is True
    assert record["producers"] == ["runner", "broker"]
