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
