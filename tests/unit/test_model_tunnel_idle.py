"""The model relay's tunnel is on every host, and waits idle where it is not used."""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO / "deploy/host/agent-ssh-tunnel.sh"
HOST_COMPOSE = REPO / "deploy/host/docker-compose.sandbox.yml"


def test_only_the_model_tunnel_may_wait_idle_without_a_destination() -> None:
    """Its service is on every host and used only by one that names the relay,
    so it waits instead of restarting forever; every other tunnel still fails."""
    services = yaml.safe_load(HOST_COMPOSE.read_text())["services"]
    optional = {
        name
        for name, service in services.items()
        if (service.get("environment") or {}).get("TUNNEL_OPTIONAL") == "1"
    }
    assert optional == {"model-tunnel"}
    assert "profiles" not in services["model-tunnel"]
    env = {"PATH": os.environ["PATH"], "TUNNEL_SSH_DESTINATION": ""}
    refused = subprocess.run(
        ["sh", str(ENTRYPOINT)], env=env, capture_output=True, text=True, timeout=10
    )
    assert refused.returncode == 1 and "TUNNEL_SSH_DESTINATION is required" in refused.stderr
    idle = subprocess.Popen(
        ["sh", str(ENTRYPOINT)],
        env={**env, "TUNNEL_OPTIONAL": "1"},
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert idle.stdout is not None
        assert "no destination configured; idle" in idle.stdout.readline()
        assert idle.poll() is None
        idle.send_signal(signal.SIGTERM)
        assert idle.wait(timeout=10) == 0
    finally:
        idle.kill()


def test_an_idle_model_tunnel_mounts_nothing_from_the_host() -> None:
    """Unset, its key and known_hosts are /dev/null, so Docker creates no stray
    directory where a key would be on a host that never had one."""
    volumes = yaml.safe_load(HOST_COMPOSE.read_text())["services"]["model-tunnel"]["volumes"]
    assert volumes == [
        "${AGENT_MODEL_TUNNEL_SSH_KEY:-/dev/null}:/etc/agent-tunnel/id:ro",
        "${AGENT_MODEL_TUNNEL_SSH_KNOWN_HOSTS:-/dev/null}:/etc/agent-tunnel/known_hosts:ro",
    ]
