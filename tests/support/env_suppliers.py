"""What supplies a production stack's environment, other than a person.

A control plane's keys come from the ``keys`` service, the commit a stack runs
is exported by the script that deploys it, and a host's ``.env`` is written by
Admin → Servers → Add server. A variable one of these supplies is not one a
deployment has to write. ``tests/unit/test_env_examples.py`` checks that every
required variable has a supplier; ``tests/unit/test_env_classification.py``
keeps the variables nothing supplies from growing.
"""

from __future__ import annotations

import re
from pathlib import Path

from cloud_agent.deployment.deployment_keys import STORED

REPO = Path(__file__).resolve().parents[2]

#: Required by a compose file, but exported by the script that runs it rather
#: than written by an operator: deploy.sh exports the commit it deploys, and
#: the release deployer the one its manifest names. A Compose command run by
#: hand still fails on it with its own message.
EXPORTED_BY_DEPLOYER = frozenset({"AGENT_DEPLOYMENT_SHA"})

#: The control-plane keys the ``keys`` service generates and keeps.
STORED_BY_KEYS = frozenset(STORED)

#: The two relays a host reaches, each spelled as a prefix in server setup.
SETUP_TUNNELS = ("AGENT_MCP_TUNNEL", "AGENT_CONTROL_PLANE_TUNNEL")


def written_by_server_setup() -> set[str]:
    """Every name automatic server setup or the host bootstrap writes into a host's .env."""
    setup = (REPO / "apps/backend/cloud_agent/application/compute_setup.py").read_text()
    bootstrap = (REPO / "apps/backend/cloud_agent/providers/compute/host_bootstrap.py").read_text()
    names = set(re.findall(r'"([A-Z][A-Z0-9_]+)":', setup + bootstrap))
    suffixes = set(re.findall(r'prefix\s*\+\s*"(_[A-Z_]+)"', setup))
    assert suffixes, (
        "server setup no longer spells the tunnel names as prefix + suffix — regex drift?"
    )
    return names | {prefix + suffix for prefix in SETUP_TUNNELS for suffix in suffixes}


def supplied() -> frozenset[str]:
    """Every variable something other than a person supplies."""
    return frozenset(written_by_server_setup()) | EXPORTED_BY_DEPLOYER | STORED_BY_KEYS
