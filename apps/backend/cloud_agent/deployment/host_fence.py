"""Read the runner pool and hold a host's deployment drain, from inside the control plane.

A deployment that rolls out a host first drains it: new work stops reaching it,
the work it has finishes, and once the host runs the new release the same
deployment releases the drain. The fence that makes this safe (the
``expected_fence_token``/``expected_fence_epoch`` compare-and-set, and the owner
token only the releasing deployment holds) is the admin API's. This command is
the same three operations without the HTTP listener and without
``ADMIN_TOKEN``: it runs in the control-plane container and uses the database
credential that container already holds, so whoever can run it already holds
the deployment::

    docker exec <project>-control-plane-1 python -m cloud_agent.deployment.host_fence snapshot
    docker exec -i <project>-control-plane-1 python -m cloud_agent.deployment.host_fence drain <host>
    docker exec -i <project>-control-plane-1 python -m cloud_agent.deployment.host_fence undrain <host>

``drain`` reads ``{"expected_fence_token", "expected_fence_epoch",
"fence_token"}`` and ``undrain`` reads ``{"fence_token"}`` from standard input,
the bodies of ``POST /admin/agent/runner-hosts/<host>/drain`` and ``/undrain``.
The tokens are never arguments, which every process on the machine can read.

Every command prints the document ``GET /admin/agent/runner-hosts`` returns.
A refusal prints ``{"status", "code", "detail"}`` on standard error, with the
status and code the API would answer, and exits 2; the input is never echoed.

The audit trail records the same actions as the API
(``agent.runner_host.drain`` and ``.undrain``) under the actor
``service:deployment-drain``: an automation, not a person.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from cloud_agent.application.host_commands import HostCommandError, HostCommands
from cloud_agent.application.host_pool_view import pool_snapshot
from cloud_agent.schemas import AcquireHostDrainFenceRequest, ReleaseHostDrainFenceRequest

if TYPE_CHECKING:
    from cloud_agent.ports import AuditRepository, HostPoolRepository

ACTOR = "service:deployment-drain"
ADMIN_IP = "local"
REFUSED = 2
MAX_INPUT_BYTES = 4096


class FenceRefused(Exception):
    """The command was refused; nothing changed."""

    def __init__(self, status: int, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


def _body[Body: BaseModel](model: type[Body], text: str) -> Body:
    """Parse a request body without ever repeating the tokens it carries."""
    if len(text.encode("utf-8")) > MAX_INPUT_BYTES:
        raise FenceRefused(422, f"standard input is over {MAX_INPUT_BYTES} bytes")
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        fields = sorted(
            {".".join(str(part) for part in error["loc"]) or "body" for error in exc.errors()}
        )
        raise FenceRefused(422, "invalid request body: " + ", ".join(fields)) from None


async def _record(audit: AuditRepository, *, drained: bool, host: str) -> None:
    """Append the audit row; like the API, a failed write never undoes the change."""
    action = "agent.runner_host.drain" if drained else "agent.runner_host.undrain"
    try:
        await audit.record_admin_action(
            action="agent.runner_host.drain" if drained else "agent.runner_host.undrain",
            actor_id=ACTOR,
            admin_ip=ADMIN_IP,
            details={"host": host},
        )
    except Exception as exc:  # the fence already changed; say so and go on
        print(
            f"warning: the audit row for {action} was not written: {type(exc).__name__}",
            file=sys.stderr,
        )


async def run(
    command: str,
    host: str | None,
    body: str,
    *,
    host_pool: HostPoolRepository,
    audit: AuditRepository,
) -> dict[str, Any]:
    """Apply one command and return the pool document the API would answer.

    Raises:
        FenceRefused: The input was invalid, or the fence refused the change.
    """
    if command in ("drain", "undrain") and not host:
        raise FenceRefused(422, f"{command} needs the host to act on")
    commands = HostCommands(host_pool)
    try:
        if command == "drain" and host:
            acquire = _body(AcquireHostDrainFenceRequest, body)
            await commands.acquire_drain_fence(
                host=host,
                expected_fence_token=acquire.expected_fence_token,
                expected_fence_epoch=acquire.expected_fence_epoch,
                fence_token=acquire.fence_token,
            )
            await _record(audit, drained=True, host=host)
        elif command == "undrain" and host:
            release = _body(ReleaseHostDrainFenceRequest, body)
            await commands.release_drain_fence(host=host, fence_token=release.fence_token)
            await _record(audit, drained=False, host=host)
    except HostCommandError as exc:
        raise FenceRefused(exc.status, str(exc), code=exc.code) from None
    return (await pool_snapshot(host_pool)).model_dump(mode="json")


async def _connected(command: str, host: str | None, body: str) -> dict[str, Any]:
    """Open the deployment's database, adopt its saved settings, and run."""
    import asyncpg

    from cloud_agent.settings import initialize_settings
    from cloud_agent.storage.assembly import Storage

    dsn = os.environ.get("AGENT_DATABASE_URL")
    if not dsn:
        raise FenceRefused(
            503, "AGENT_DATABASE_URL is not set; run this in the control-plane container"
        )
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1)
    try:
        storage = Storage(pool)
        # Read-only adoption: the pool document names the execution profile,
        # which is a saved setting. Never seed: that is the main listener's job.
        await initialize_settings(storage.settings, seed=False)
        return await run(command, host, body, host_pool=storage.host_pool, audit=storage.audit)
    finally:
        await pool.close()


def main(argv: list[str] | None = None) -> int:
    """Run one command; print the pool, or the refusal on standard error."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("snapshot", help="Print the runner pool.")
    for name, help_text in (
        ("drain", "Acquire the deployment drain; the fence tokens arrive on standard input."),
        ("undrain", "Release this deployment's drain; its token arrives on standard input."),
    ):
        commands.add_parser(name, help=help_text).add_argument("host")
    args = parser.parse_args(argv)
    host = getattr(args, "host", None)
    body = sys.stdin.read(MAX_INPUT_BYTES + 1) if host is not None else ""
    try:
        document = asyncio.run(_connected(args.command, host, body))
    except FenceRefused as exc:
        print(
            json.dumps({"status": exc.status, "code": exc.code, "detail": exc.detail}),
            file=sys.stderr,
        )
        return REFUSED
    print(json.dumps(document, separators=(",", ":")))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by deployment
    sys.exit(main())
