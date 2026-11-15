"""A relay's tunnel has a way out, and GitHub's names stay on the relay network.

The relay network is route-less: a tunnel on that network alone never reaches
the control plane's machine, and restarts forever. Real Docker proves the
tunnels start (``tests/external/test_relay_tunnel_upstream_real_docker.py``);
this reads the Compose file, so the gate that runs on every change says which
service lost its way out.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((REPO / "deploy/host/docker-compose.sandbox.yml").read_text())
SERVICES = COMPOSE["services"]
RELAY_NETWORK = "agent-relays"


def _routed(network: str) -> bool:
    return (COMPOSE["networks"][network] or {}).get("internal") is not True


def _attachments(service: dict) -> dict[str, dict]:
    """A service's networks by name, whether the file lists or maps them."""
    networks = service.get("networks", {})
    if isinstance(networks, list):
        return {name: {} for name in networks}
    return {name: attachment or {} for name, attachment in networks.items()}


def test_every_relay_tunnel_joins_a_network_with_a_route() -> None:
    assert not _routed(RELAY_NETWORK), "the relay network must stay route-less"
    relays = {
        name: set(_attachments(service))
        for name, service in SERVICES.items()
        if RELAY_NETWORK in _attachments(service)
    }
    assert set(relays) == {"mcp-tunnel", "github-tunnel", "model-tunnel"}
    stranded = sorted(name for name, networks in relays.items() if not any(map(_routed, networks)))
    assert not stranded, (
        f"{stranded} join only route-less networks: ssh cannot resolve or reach the "
        "control plane's machine from there"
    )


def test_the_github_names_are_aliases_on_the_relay_network_only() -> None:
    """The runner is on the stack's own network and reaches GitHub itself."""
    named = {
        (service_name, network)
        for service_name, service in SERVICES.items()
        for network, attachment in _attachments(service).items()
        if {"github.com", "api.github.com"} & set(attachment.get("aliases", ()))
    }
    assert named == {("github-tunnel", RELAY_NETWORK)}
