"""Every relay tunnel in the host stack reaches its far host, on real Docker.

A relay tunnel has two legs: the relay network, where group lifecycle finds it
and from where it is attached to each group's bridge, and SSH to the control
plane's machine. The relay network is declared ``internal: true``: it has no
route and resolves no name outside itself. So a tunnel that joins nothing else
never finds its far host, and ``restart: unless-stopped`` restarts it forever
while the deployment that started it reports success.

That is how the GitHub relay's tunnel first met a real host (staging,
2026-11-15): ``ssh: Could not resolve hostname``, eighteen restarts, and every
sandbox on the host resolving ``github.com`` to a container that answered
nothing. ``docker compose config`` renders such a file without complaint, and
the sandbox network test gives its stand-in relays an upstream network by
hand, so nothing had started these services as the file wires them.

This starts them from the shipped Compose file, against a far host that is an
sshd with one listener per relay, joined to the stack's own network under a
name only that network resolves. Then a container on the relay network, as a
group's bridge would carry the relay, asks each relay's alias for its
listener's answer.

Run by hand with ``pytest -m external
tests/external/test_relay_tunnel_upstream_real_docker.py``. Needs Docker with
Compose and the image ``alpine:3.21`` (``AGENT_RELAY_TUNNEL_TEST_IMAGE``
overrides it); it builds the tunnel image from ``deploy/host`` and a far-host
image from the same base, and removes its containers and networks afterwards.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.external

REPO = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO / "deploy/host/docker-compose.sandbox.yml"
BASE = os.environ.get("AGENT_RELAY_TUNNEL_TEST_IMAGE", "alpine:3.21")
FAR_HOST_IMAGE = "cloud-agent-test-far-host:local"
#: The far host's name, which only the stack's own network resolves.
FAR_HOST = "far-host"
READY_TIMEOUT_S = 90

#: An sshd that forwards, one account whose key it writes out for the tunnels,
#: and a listener per relay on loopback that answers with the relay's name.
FAR_HOST_DOCKERFILE = f"""\
FROM {BASE}
RUN apk add --no-cache openssh-server openssh-keygen \\
    && adduser -D tunnel && passwd -u tunnel \\
    && ssh-keygen -q -t ed25519 -N '' -f /etc/ssh/ssh_host_ed25519_key
COPY far-host.sh /far-host.sh
ENTRYPOINT ["/bin/sh", "/far-host.sh"]
"""
FAR_HOST_SCRIPT = f"""\
set -eu
ssh-keygen -q -t ed25519 -N '' -f /out/id
install -d -m 700 -o tunnel -g tunnel /home/tunnel/.ssh
install -m 600 -o tunnel -g tunnel /out/id.pub /home/tunnel/.ssh/authorized_keys
for listener in "$@"; do
    nc -lk -s 127.0.0.1 -p "${{listener%%=*}}" -e /bin/echo "${{listener#*=}}" &
done
# Written last: the test waits for it, and by then the key is in place.
printf '{FAR_HOST} %s\\n' "$(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)" > /out/known_hosts
exec /usr/sbin/sshd -D -e -o AllowTcpForwarding=yes -o PasswordAuthentication=no
"""


@dataclass(frozen=True)
class Relay:
    """One relay tunnel: what a group calls it, and where its far end listens."""

    prefix: str
    alias: str
    listen_port: int
    far_port: int


#: The ports are the Compose file's defaults, so a default that moved fails here.
RELAYS = {
    "mcp-tunnel": Relay("AGENT_MCP_TUNNEL", "agent-mcp", 8000, 8001),
    "github-tunnel": Relay("AGENT_GITHUB_TUNNEL", "github.com", 443, 8443),
    "model-tunnel": Relay("AGENT_MODEL_TUNNEL", "agent-models", 8000, 8002),
}


def _docker(*args: str, check: bool = True, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check, **kwargs)


def _stack_environment(project: str, keys: Path) -> str:
    """The stack's ``.env``: every tunnel named, and the rest the file requires."""
    lines = [
        f"COMPOSE_PROJECT={project}",
        "AGENT_SANDBOX_MANAGER_TOKEN=relay-tunnel-test-not-a-secret",
        "AGENT_SANDBOX_IMAGE=unused:relay-tunnel-test",
        "AGENT_EGRESS_CHAT_NETWORK_POOL=10.240.0.0/16",
        "AGENT_EGRESS_DNS_SERVER=192.0.2.53",
    ]
    for prefix in ("AGENT_CONTROL_PLANE_TUNNEL", *(relay.prefix for relay in RELAYS.values())):
        lines += [
            f"{prefix}_SSH_DESTINATION=tunnel@{FAR_HOST}",
            f"{prefix}_SSH_KEY={keys / 'id'}",
            f"{prefix}_SSH_KNOWN_HOSTS={keys / 'known_hosts'}",
        ]
    return "\n".join(lines) + "\n"


@dataclass
class Stack:
    project: str
    environment: Path

    def compose(self, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return _docker(
            "compose", "-p", self.project, "-f", str(COMPOSE_FILE),
            "--env-file", str(self.environment), "--profile", "github-relay", *args,
            check=check,
        )  # fmt: skip

    def container(self, service: str) -> str:
        return self.compose("ps", "-a", "-q", service).stdout.strip()

    def state(self, service: str) -> str:
        """A service's container state and its last output, for a failure message."""
        container = self.container(service)
        if not container:
            return f"{service}: no container"
        state = _docker(
            "inspect", container,
            "--format", "{{.State.Status}}, restarted {{.RestartCount}} times",
            check=False,
        ).stdout.strip()  # fmt: skip
        logs = _docker("logs", "--tail", "5", container, check=False)
        return f"{service}: {state}\n{(logs.stdout + logs.stderr).strip()}"


@pytest.fixture(scope="module")
def stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    if shutil.which("docker") is None or _docker("compose", "version", check=False).returncode:
        pytest.skip("needs Docker with Compose")
    if _docker("image", "inspect", BASE, check=False).returncode:
        pytest.skip(f"needs the image {BASE}")

    work = tmp_path_factory.mktemp("relay-tunnels")
    context, keys = work / "far-host", work / "keys"
    context.mkdir()
    keys.mkdir()
    (context / "Dockerfile").write_text(FAR_HOST_DOCKERFILE)
    (context / "far-host.sh").write_text(FAR_HOST_SCRIPT)
    _docker("build", "-q", "-t", FAR_HOST_IMAGE, str(context))

    project = f"relay-tunnel-test-{secrets.token_hex(4)}"
    far_host = f"{project}-far-host"
    environment = work / "stack.env"
    environment.write_text(_stack_environment(project, keys))
    stack = Stack(project, environment)
    try:
        _docker(
            "run", "-d", "--name", far_host, "-v", f"{keys}:/out", FAR_HOST_IMAGE,
            *(f"{relay.far_port}={service}" for service, relay in RELAYS.items()),
        )  # fmt: skip
        deadline = time.monotonic() + READY_TIMEOUT_S
        while not (keys / "known_hosts").exists():
            if time.monotonic() > deadline:
                logs = _docker("logs", far_host, check=False)
                pytest.fail(f"the far host did not start:\n{logs.stdout}{logs.stderr}")
            time.sleep(0.5)

        # Create the stack's networks without starting anything, so the far
        # host is already on the stack's own network when the tunnels first
        # look for it. It joins no other network of the stack.
        stack.compose("up", "--no-start", "--build", *RELAYS)
        _docker("network", "connect", "--alias", FAR_HOST, f"{project}_internal", far_host)
        stack.compose("up", "-d", "--no-deps", *RELAYS)
        yield stack
    finally:
        # The far host first: a network one of its endpoints is still on stays.
        _docker("rm", "-f", "--volumes", far_host, check=False)
        stack.compose("down", "--volumes", "--remove-orphans", check=False)
        # The far host wrote the key as root; give the directory back.
        _docker("run", "--rm", "-v", f"{keys}:/out", BASE, "sh", "-c", "rm -f /out/*", check=False)


def test_the_test_covers_every_relay_the_stack_declares() -> None:
    """A relay added to the Compose file is added here, or this fails."""
    import yaml

    services = yaml.safe_load(COMPOSE_FILE.read_text())["services"]
    on_relay_network = {
        name for name, service in services.items() if "agent-relays" in service.get("networks", {})
    }
    assert on_relay_network == set(RELAYS)


@pytest.mark.parametrize("service", sorted(RELAYS))
def test_a_relay_tunnel_reaches_its_far_host(stack: Stack, service: str) -> None:
    """Asked from the relay network, the relay's alias answers as its far listener."""
    relay = RELAYS[service]
    network = f"{stack.project}-agent-relays"
    inspected = json.loads(_docker("network", "inspect", network).stdout)[0]
    assert inspected["Internal"] is True, "the relay network must stay route-less"

    answer = ""
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        asked = _docker(
            "run", "--rm", "--network", network, BASE,
            "nc", "-w", "5", relay.alias, str(relay.listen_port),
            check=False,
        )  # fmt: skip
        answer = asked.stdout.strip()
        if answer == service:
            break
        time.sleep(2)
    assert answer == service, (
        f"{relay.alias}:{relay.listen_port} on the relay network did not reach the far host's "
        f"listener on {relay.far_port} (last answer: {answer!r}).\n{stack.state(service)}"
    )
    # Reached once is not enough: a tunnel that connects and dies is restarted
    # and answers again. It must still be the container that first started.
    assert stack.state(service).startswith(f"{service}: running, restarted 0 times")
