"""
Tests for the persistent evidence log and its aggregator.

The instrumentation exists so future real runs accumulate proof behind the
resolution-rate number. Two properties matter most and are pinned here:

  - it must NEVER be able to break a render (unwritable path, unserializable
    payload, disabled flag — all must degrade silently);
  - the aggregator must compute the same metric shape as eval/run_corpus.py, so
    a production run can confirm or refute the corpus number.

Zero LLM calls, zero subprocess calls.
"""

import json
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from eval.aggregate_logs import aggregate  # noqa: E402
from manimgen.validator.evidence_log import (  # noqa: E402
    evidence_enabled,
    evidence_path,
    log_event,
    read_events,
)


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MANIMGEN_EVIDENCE_DIR", str(tmp_path))
    monkeypatch.setenv("MANIMGEN_EVIDENCE_LOG", "1")
    return tmp_path


@pytest.mark.unit
class TestEvidenceLog:

    def test_writes_and_reads_back_an_event(self, log_dir):
        assert log_event("precheck", scene="s01.py", validation_clean=True)
        events = read_events()
        assert len(events) == 1
        assert events[0]["event"] == "precheck"
        assert events[0]["scene"] == "s01.py"
        assert "ts" in events[0]

    def test_appends_rather_than_overwrites(self, log_dir):
        for i in range(5):
            log_event("precheck", scene=f"s{i}.py", validation_clean=True)
        assert len(read_events()) == 5

    def test_each_event_is_one_json_line(self, log_dir):
        log_event("precheck", scene="a.py", validation_clean=False)
        log_event("av_mismatch", output="a.mp4", diff=2.0)
        lines = evidence_path()
        with open(lines) as f:
            raw = [ln for ln in f.read().splitlines() if ln.strip()]
        assert len(raw) == 2
        for ln in raw:
            json.loads(ln)  # must parse standalone

    def test_disabled_by_env_writes_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MANIMGEN_EVIDENCE_DIR", str(tmp_path))
        monkeypatch.setenv("MANIMGEN_EVIDENCE_LOG", "0")
        assert not evidence_enabled()
        assert log_event("precheck", scene="x.py") is False
        assert read_events() == []

    def test_unwritable_path_never_raises(self, tmp_path, monkeypatch):
        """Instrumentation must not be able to fail a render."""
        blocker = tmp_path / "blocked"
        blocker.write_text("i am a file, not a directory")
        monkeypatch.setenv("MANIMGEN_EVIDENCE_DIR", str(blocker / "sub"))
        monkeypatch.setenv("MANIMGEN_EVIDENCE_LOG", "1")
        assert log_event("precheck", scene="x.py") is False  # no exception

    def test_unserializable_payload_never_raises(self, log_dir):
        assert log_event("precheck", obj=object()) is True  # default=str coerces

    def test_oversized_event_is_truncated_not_dropped(self, log_dir):
        assert log_event("precheck", blob="x" * 50000)
        events = read_events()
        assert len(events) == 1
        assert events[0].get("truncated") is True

    def test_malformed_line_is_skipped(self, log_dir):
        log_event("precheck", scene="good.py", validation_clean=True)
        with open(evidence_path(), "a") as f:
            f.write("{ this is not json\n")
        events = read_events()
        assert len(events) == 1
        assert events[0]["scene"] == "good.py"

    def test_missing_log_reads_as_empty(self, log_dir):
        assert read_events(str(log_dir / "does_not_exist.jsonl")) == []


@pytest.mark.integration
class TestCodeguardWiring:
    """precheck_and_autofix_file must emit an event without changing behavior."""

    def test_precheck_emits_event_and_preserves_return_contract(self, log_dir, tmp_path):
        from manimgen.validator.codeguard import precheck_and_autofix_file

        scene = tmp_path / "s01.py"
        scene.write_text(
            "from manim import *\n\n\nclass S(Scene):\n"
            "    def construct(self):\n        self.play(Create(Circle()))\n"
        )
        result = precheck_and_autofix_file(str(scene))

        assert set(result) >= {"ok", "stderr", "layout_warnings"}
        assert result["ok"] is True

        prechecks = [e for e in read_events() if e["event"] == "precheck"]
        assert len(prechecks) == 1
        assert prechecks[0]["scene"] == "s01.py"
        assert prechecks[0]["code_changed"] is True
        assert prechecks[0]["validation_clean"] is True
        assert prechecks[0]["rules_fired_count"] > 0

    def test_precheck_records_validation_failure(self, log_dir, tmp_path):
        from manimgen.validator.codeguard import precheck_and_autofix_file

        scene = tmp_path / "bad.py"
        scene.write_text("from manimlib import *\n\ndef broken(:\n    pass\n")
        result = precheck_and_autofix_file(str(scene))

        assert result["ok"] is False
        prechecks = [e for e in read_events() if e["event"] == "precheck"]
        assert prechecks[0]["validation_clean"] is False
        assert prechecks[0]["error_count"] >= 1

    def test_public_precheck_still_returns_str(self):
        """The refactor must not change precheck_and_autofix's contract."""
        from manimgen.validator.codeguard import precheck_and_autofix

        out = precheck_and_autofix("from manim import *\n")
        assert isinstance(out, str)
        assert "from manimlib import *" in out


@pytest.mark.unit
class TestAggregator:

    def test_computes_resolution_rate(self):
        events = [
            {"event": "precheck", "validation_clean": True, "code_changed": True,
             "rules_fired": ["fixed import (1)"]},
            {"event": "precheck", "validation_clean": True, "code_changed": False,
             "rules_fired": []},
            {"event": "precheck", "validation_clean": False, "code_changed": True,
             "rules_fired": ["fixed import (2)"], "first_error": "SyntaxError: bad (line 3)"},
        ]
        s = aggregate(events)
        assert s["resolution"]["precheck_events"] == 3
        assert s["resolution"]["validation_clean"] == 2
        assert s["resolution"]["rate_pct"] == 66.7

    def test_rule_counts_ignore_occurrence_suffix(self):
        """'fixed import (1)' and 'fixed import (2)' are the same rule."""
        events = [
            {"event": "precheck", "validation_clean": True,
             "rules_fired": ["fixed import (1)"]},
            {"event": "precheck", "validation_clean": True,
             "rules_fired": ["fixed import (7)"]},
        ]
        assert aggregate(events)["rules_fired"] == {"fixed import": 2}

    def test_aggregates_shadow_and_mismatch_events(self):
        events = [
            {"event": "shadow_unknown_symbols", "symbols": ["Frobnicate", "Blorp"]},
            {"event": "shadow_invalid_kwargs",
             "kwargs": [{"class": "Surface", "kwarg": "fill_color", "lineno": 4}]},
            {"event": "av_mismatch", "diff": 2.4, "large": True},
            {"event": "av_mismatch", "diff": 1.1, "large": False},
        ]
        s = aggregate(events)
        assert s["shadow"]["unknown_symbol_events"] == 1
        assert s["shadow"]["top_unknown_symbols"]["Frobnicate"] == 1
        assert s["shadow"]["top_invalid_kwargs"]["Surface(...fill_color=)"] == 1
        assert s["av_mismatch"] == {"count": 2, "large": 1}

    def test_empty_log_does_not_divide_by_zero(self):
        s = aggregate([])
        assert s["resolution"]["rate_pct"] == 0.0
        assert s["total_events"] == 0

    def test_metric_is_comparable_to_corpus_harness(self, log_dir, tmp_path):
        """Aggregator must key off the same validation_clean signal the corpus uses."""
        from manimgen.validator.codeguard import precheck_and_autofix_file

        good = tmp_path / "good.py"
        good.write_text(
            "from manim import *\n\n\nclass S(Scene):\n"
            "    def construct(self):\n        self.play(Create(Circle()))\n"
        )
        precheck_and_autofix_file(str(good))

        s = aggregate(read_events())
        assert s["resolution"]["precheck_events"] == 1
        assert s["resolution"]["rate_pct"] == 100.0
