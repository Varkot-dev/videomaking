"""The suite is offline: a test that opens a real network connection fails.

conftest._block_external_network turns that promise into a check. Without it,
test_cue_tokenization streamed from Microsoft's speech service: it failed with
HTTP 403 on GitHub's runners and with a connection error behind a restricted
network, and passed only on a developer's machine.

The external address below is from TEST-NET-3 (RFC 5737), reserved for
documentation and never routed. The guard refuses it before any packet is sent,
so these tests need no network and never wait on one.
"""

from __future__ import annotations

import os
import socket

import pytest

_EXTERNAL = ("203.0.113.1", 80)


def _tcp() -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)  # a missing guard must fail fast, not hang
    return sock


def test_connect_to_an_external_address_is_refused():
    sock = _tcp()
    try:
        with pytest.raises(RuntimeError, match="real network connection"):
            sock.connect(_EXTERNAL)
    finally:
        sock.close()


def test_connect_ex_is_refused_too():
    sock = _tcp()
    try:
        with pytest.raises(RuntimeError, match="real network connection"):
            sock.connect_ex(_EXTERNAL)
    finally:
        sock.close()


def test_the_message_names_the_address_and_the_fix():
    sock = _tcp()
    try:
        with pytest.raises(RuntimeError) as err:
            sock.connect(_EXTERNAL)
    finally:
        sock.close()
    assert "203.0.113.1" in str(err.value)
    assert "mock" in str(err.value)


def test_loopback_still_works():
    # Flask test clients, local servers and asyncio's internal socketpair
    # must keep working.
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = _tcp()
    try:
        client.connect(("127.0.0.1", server.getsockname()[1]))
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize(
    "name", ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"]
)
def test_proxy_variables_are_cleared(name):
    # A proxy usually listens on loopback, so a proxy-honoring client would
    # otherwise reach the internet through an "allowed" localhost connection.
    assert name not in os.environ
