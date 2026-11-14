"""A made-up host name fails at once, as it does on CI (see ``tests/conftest.py``)."""

from __future__ import annotations

import asyncio
import socket
import time

import pytest

# Far below the seconds a slow resolver takes, far above a refused call.
_AT_ONCE = 1.0


@pytest.mark.parametrize(
    "host",
    [
        "sandbox_manager.test",
        "SANDBOX-MANAGER.TEST.",
        b"sandbox_manager.test",
        "unresolvable.invalid",
        "host.example",
        "control",
        "sandbox-manager-a",
    ],
)
def test_a_name_no_resolver_answers_fails_at_once(host: str | bytes) -> None:
    started = time.monotonic()
    with pytest.raises(socket.gaierror) as raised:
        socket.getaddrinfo(host, 8092)
    assert raised.value.errno == socket.EAI_NONAME
    assert time.monotonic() - started < _AT_ONCE


async def test_an_event_loop_lookup_fails_at_once_too() -> None:
    started = time.monotonic()
    with pytest.raises(socket.gaierror):
        await asyncio.get_running_loop().getaddrinfo("sandbox_manager.test", 8092)
    assert time.monotonic() - started < _AT_ONCE


@pytest.mark.parametrize("host", ["localhost", "LOCALHOST.", "127.0.0.1", "::1"])
def test_localhost_and_addresses_still_resolve(host: str) -> None:
    try:
        found = socket.getaddrinfo(host, 80, type=socket.SOCK_STREAM)
    except socket.gaierror:
        if ":" in host and not socket.has_ipv6:
            pytest.skip("this host has no IPv6")
        raise
    assert found
