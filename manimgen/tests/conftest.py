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

import pytest


@pytest.fixture(autouse=True)
def _isolate_evidence_log(tmp_path, monkeypatch):
    """Redirect the evidence log into this test's tmp_path by default."""
    monkeypatch.setenv("MANIMGEN_EVIDENCE_DIR", str(tmp_path / "evidence"))


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
