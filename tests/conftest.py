"""Every test starts from CI's environment, whatever shell or earlier test ran first.

CI runs the tests with a umask of 022 and no sandbox CLI version in the
environment. A developer's shell or an enclosing agent can differ in both: a
restrictive umask changes the modes of the files tests create and check, and
an inherited ``CODEX_VERSION`` reads to the deploy scripts as an operator's
version pin. A test can differ too: one that runs a CLI's ``main()`` in-process
takes on whatever umask that CLI sets for itself, and keeps it for every later
test in the same worker.

CI's resolver also answers at once that a made-up host name does not exist.
Many tests hand code a name such as ``sandbox_manager.test`` or ``control``
and let its connection fail; a developer's resolver can take many seconds to
give the same answer, and did: 15 seconds a lookup turned a three-minute
``make check`` into twenty. Such names fail here without a lookup.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

# RFC 6761 reserves these top-level names: no resolver anywhere answers them.
_RESERVED_SUFFIXES = ("test", "invalid", "example")

# The markers of the tiers that reach real servers, whose names stay real.
_NETWORK_MARKERS = ("external", "dbtest")


def pytest_configure() -> None:
    """Give this process CI's values before collection.

    Code a test module runs at import then sees the same values as its tests.
    """
    os.umask(0o022)
    os.environ.pop("CODEX_VERSION", None)


@pytest.fixture(autouse=True)
def _ci_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Give each test CI's values, and leave them for whatever runs next."""
    os.umask(0o022)
    monkeypatch.delenv("CODEX_VERSION", raising=False)
    yield
    # Restored rather than left as the test left it, so a test that changed it
    # cannot decide the modes the next module's fixtures create files with.
    os.umask(0o022)


def never_resolves(host: str | bytes | None) -> bool:
    """Whether ``host`` is a name no resolver answers, CI's included.

    That is a name under a reserved top-level name, or a single label other
    than ``localhost``, which only a search domain could complete. Addresses,
    ``localhost`` and every other name are left to the real resolver.
    """
    if host is None:
        return False
    name = (host.decode("ascii", "replace") if isinstance(host, bytes) else host).lower()
    name = name.rstrip(".")
    if not name or name == "localhost" or ":" in name:
        return False
    try:
        ipaddress.ip_address(name)
    except ValueError:
        pass
    else:
        return False
    if "." not in name:
        return True
    return name.rsplit(".", 1)[1] in _RESERVED_SUFFIXES


@pytest.fixture(autouse=True)
def _ci_name_resolution(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail a name that never resolves as CI's resolver does: at once."""
    if any(request.node.get_closest_marker(marker) for marker in _NETWORK_MARKERS):
        return
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host: str | bytes | None, *args: Any, **kwargs: Any) -> Any:
        if never_resolves(host):
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return real_getaddrinfo(host, *args, **kwargs)

    # asyncio's and anyio's lookups call this module attribute too.
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
