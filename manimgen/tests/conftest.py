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
# LLM provider guard
# ---------------------------------------------------------------------------
# claude_cli is the default LLM provider, so any test that reaches chat()
# without mocking it would start the real `claude` program on a developer's
# machine and spend Claude plan allowance on every local test run (measured: 4
# launches per run from tests/test_director.py before this guard). Three layers
# stop that, and the same for the paid and local providers:
#
# 1. `shutil.which` cannot find claude, by bare name or by full path (so an
#    llm.claude_cli_path set to /usr/local/bin/claude is covered too). Unmocked
#    chat() then fails with the same "not found on PATH" error as before.
# 2. llm._run_cli, the one function that spawns a process, refuses any command
#    whose program is claude. This catches a test that patches `which` to a
#    fake path but forgets to mock the subprocess.
# 3. llm._anthropic, llm._gemini and llm._ollama raise. The socket guard above
#    already blocks the paid APIs, but Ollama runs on loopback, so a developer
#    with a local Ollama server would otherwise get real calls.
#
# A test that patches one of these seams itself overrides the guard, because
# its patch is applied after this fixture. A test that drives the real
# provider function with its transport mocked (fake SDK module, patched
# requests.post) opts out of layer 3 with @pytest.mark.allow_real_llm. Layers
# 1 and 2 have no opt-out: a test that needs claude patches `which` and
# llm._run_cli itself.

_CLAUDE_NAMES = {"claude", "claude.exe", "claude.cmd", "claude.bat", "claude.ps1"}


def _is_claude_program(name) -> bool:
    import os

    base = os.path.basename(str(name).replace("\\", "/")).lower()
    return base in _CLAUDE_NAMES


class RealLlmCallInTest(RuntimeError):
    """A test reached a real LLM provider without mocking it."""


@pytest.fixture(autouse=True)
def _never_launch_real_claude(request, monkeypatch):
    import shutil

    real_which = shutil.which

    def which(name, *args, **kwargs):
        if _is_claude_program(name):
            return None
        return real_which(name, *args, **kwargs)

    monkeypatch.setattr(shutil, "which", which)

    import manimgen.llm as llm_mod

    real_run_cli = llm_mod._run_cli

    def guarded_run_cli(cmd, *args, **kwargs):
        if cmd and _is_claude_program(cmd[0]):
            raise RealLlmCallInTest(
                f"Test tried to launch the real Claude CLI ({cmd[0]!r}). Mock "
                "manimgen.llm._run_cli or llm.chat instead."
            )
        return real_run_cli(cmd, *args, **kwargs)

    monkeypatch.setattr(llm_mod, "_run_cli", guarded_run_cli)

    if request.node.get_closest_marker("allow_real_llm"):
        return

    for name in ("_anthropic", "_gemini", "_ollama"):

        def blocked(*args, _name=name, **kwargs):
            raise RealLlmCallInTest(
                f"Test reached the real LLM provider llm.{_name}(). Mock "
                f"llm.chat or llm.{_name}, or mark the test "
                "@pytest.mark.allow_real_llm if it mocks the transport itself."
            )

        monkeypatch.setattr(llm_mod, name, blocked)


# ---------------------------------------------------------------------------
# LLM usage ledger
# ---------------------------------------------------------------------------
# chat() appends one JSONL record per call under paths.logs_dir(). Keep tests
# from writing to the real output/logs and start each with an empty ledger.


@pytest.fixture(autouse=True)
def _isolate_llm_ledger(tmp_path, monkeypatch):
    import manimgen.llm as llm_mod

    monkeypatch.setattr(llm_mod, "_ledger_dir", lambda: str(tmp_path / "llm_logs"))
    llm_mod.reset_usage()
    yield
    llm_mod.reset_usage()
