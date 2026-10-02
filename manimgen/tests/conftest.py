"""Shared pytest fixtures.

Keeps the suite hermetic with respect to the evidence log added alongside the
Codeguard instrumentation: `precheck_and_autofix_file` now appends a JSONL event
on every call, and many existing tests exercise that function. Without this
redirect those tests would append to the developer's real `output/logs/`,
polluting production evidence with synthetic fixture data — which is exactly the
failure mode the instrumentation is meant to avoid.

Tests that specifically assert on log contents override MANIMGEN_EVIDENCE_DIR
themselves (see tests/test_evidence_log.py).
"""

import socket as _socket

import pytest


@pytest.fixture(autouse=True)
def _isolate_evidence_log(tmp_path, monkeypatch):
    """Redirect the evidence log into this test's tmp_path by default."""
    monkeypatch.setenv("MANIMGEN_EVIDENCE_DIR", str(tmp_path / "evidence"))


# ---------------------------------------------------------------------------
# Network guard
# ---------------------------------------------------------------------------
# The suite promises to be fully offline (every LLM, TTS and render seam is
# mocked). This guard turns that promise into a check: any test that tries to
# open a socket to a non-loopback host fails immediately with a clear message,
# instead of passing on a dev machine and failing (or hanging) on a locked-down
# network such as a library PC or CI. Loopback and AF_UNIX stay allowed so
# Flask test clients, local servers and asyncio's internal socketpair work.
# Proxy env vars are cleared for each test: a proxy usually listens on
# loopback, so a proxy-honoring client (requests, httpx) would otherwise reach
# the internet through an "allowed" localhost connection.

_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0", "::", ""}


def _is_loopback(address) -> bool:
    if not isinstance(address, tuple) or not address:
        return True  # AF_UNIX path or other non-IP address family
    host = str(address[0]).lower()
    return host in _LOOPBACK_HOSTS or host.startswith("127.")


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch):
    """Fail any test that connects a socket to a non-loopback host."""
    for var in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv(var.lower(), raising=False)
    real_connect = _socket.socket.connect
    real_connect_ex = _socket.socket.connect_ex

    def _check(address):
        if not _is_loopback(address):
            raise RuntimeError(
                f"Test attempted a real network connection to {address!r}. "
                "The test suite must be offline: mock the client "
                "(edge_tts.Communicate, llm.chat, etc.) instead."
            )

    def guarded_connect(self, address):
        _check(address)
        return real_connect(self, address)

    def guarded_connect_ex(self, address):
        _check(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(_socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(_socket.socket, "connect_ex", guarded_connect_ex)


# ---------------------------------------------------------------------------
# Claude CLI guard
# ---------------------------------------------------------------------------
# claude_cli is the default LLM provider, so any test that reaches chat()
# without mocking it would start the real `claude` program on a developer's
# machine and spend Claude plan allowance on every local test run (measured: 4
# launches per run from tests/test_director.py before this guard). Make the
# program unfindable by default so those tests fail the same way they did when
# no API key was set, and nothing is ever launched. Tests that exercise the
# provider patch `which` themselves (and mock the subprocess), which overrides
# this.


@pytest.fixture(autouse=True)
def _never_launch_real_claude(monkeypatch):
    import shutil

    real_which = shutil.which

    def which(name, *args, **kwargs):
        if str(name).lower() in {"claude", "claude.exe", "claude.cmd"}:
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)
