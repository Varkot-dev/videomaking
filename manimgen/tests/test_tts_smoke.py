"""Unit tests for scripts/tts_smoke.py (pure logic and a mocked end-to-end run).

No network: the narration call and ffprobe are replaced, so the default suite
stays offline (tests/conftest.py blocks real sockets on purpose). The real
service is only exercised by running the script itself (nightly CI job).
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

import pytest

from manimgen.renderer.tts import WordTimestamp

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SCRIPT = os.path.join(_ROOT, "scripts", "tts_smoke.py")
_spec = importlib.util.spec_from_file_location("tts_smoke", _SCRIPT)
ts = importlib.util.module_from_spec(_spec)
sys.modules["tts_smoke"] = ts
_spec.loader.exec_module(ts)


def _fake_timestamps(text: str, per_word: float = 0.4, split_hyphens=False):
    """Boundaries like the service: punctuation dropped, one per word."""
    words = []
    for raw in text.split():
        w = raw.strip(".,!?")
        parts = w.split("-") if split_hyphens else [w]
        words.extend(p for p in parts if p)
    out, t = [], 0.1
    for w in words:
        out.append(WordTimestamp(word=w, start=t, end=t + per_word - 0.05))
        t += per_word
    return out


# ---------------------------------------------------------------------------
# duration bounds
# ---------------------------------------------------------------------------


def test_duration_bounds_scale_with_words():
    lo, hi = ts.duration_bounds(30)
    assert lo == pytest.approx(30 / ts.MAX_WORDS_PER_SEC)
    assert hi == pytest.approx(30 / ts.MIN_WORDS_PER_SEC + ts.SLACK_SECONDS)
    assert ts.duration_bounds(0)[0] > 0  # zero words never gives a zero floor


@pytest.mark.parametrize(
    "duration,status",
    [
        (11.0, ts.PASS),  # 28 words at ~2.5 wps
        (28 / 5.0 - 0.1, ts.FAIL),  # faster than anyone speaks
        (28 / 1.2 + 2.0 + 0.1, ts.FAIL),  # mostly silence
        (None, ts.FAIL),  # ffprobe could not read it
    ],
)
def test_evaluate_duration(duration, status):
    assert ts.evaluate_duration(duration, 28).status == status


# ---------------------------------------------------------------------------
# word coverage
# ---------------------------------------------------------------------------


def test_coverage_pass_when_tokens_match():
    spoken = "Don't forget 16 numbers.".split()
    got = ts.evaluate_word_coverage(spoken, ["Don't", "forget", "16", "numbers"])
    assert got.status == ts.PASS


def test_coverage_fail_when_empty_or_missing_words():
    spoken = "one two three".split()
    assert ts.evaluate_word_coverage(spoken, []).status == ts.FAIL
    assert ts.evaluate_word_coverage(spoken, ["one", "two"]).status == ts.FAIL


def test_coverage_warn_when_hyphen_split_into_two_tokens():
    spoken = "a well-known trick".split()
    c = ts.evaluate_word_coverage(spoken, ["a", "well", "known", "trick"])
    assert c.status == ts.WARN and "counts differ" in c.detail


def test_coverage_warn_when_text_is_normalised():
    spoken = "holds 16 numbers".split()
    c = ts.evaluate_word_coverage(spoken, ["holds", "sixteen", "numbers"])
    assert c.status == ts.WARN


# ---------------------------------------------------------------------------
# monotonic and within duration
# ---------------------------------------------------------------------------


def test_monotonic_pass():
    spans = [(0.1, 0.4), (0.5, 0.9), (0.9, 1.2)]
    assert ts.evaluate_monotonic(spans, 1.4).status == ts.PASS


@pytest.mark.parametrize(
    "spans",
    [
        [],
        [(0.5, 0.9), (0.2, 0.4)],  # start goes backwards
        [(0.5, 0.4)],  # ends before it starts
        [(-0.1, 0.4)],  # negative start
    ],
)
def test_monotonic_fail(spans):
    assert ts.evaluate_monotonic(spans, 5.0).status == ts.FAIL


def test_monotonic_fail_past_audio_end_with_tolerance():
    dur = 2.0
    assert ts.evaluate_monotonic([(0.1, 2.2)], dur).status == ts.PASS  # in tolerance
    assert ts.evaluate_monotonic([(0.1, 2.0 + 0.3)], dur).status == ts.FAIL


# ---------------------------------------------------------------------------
# cue mapping
# ---------------------------------------------------------------------------


def test_cues_pass_and_fail():
    spoken = "a b c d e".split()
    words = ["a", "b", "c", "d", "e"]
    segs = [(0.0, 1.0), (1.0, 1.0)]
    ok = ts.evaluate_cues(spoken, [0, 2], [0, 2], words, segs, 2.0)
    assert ok.status == ts.PASS
    # cue resolved to the wrong word
    assert ts.evaluate_cues(spoken, [0, 2], [0, 3], words, segs, 2.0).status == ts.FAIL
    # segments do not cover the audio
    assert ts.evaluate_cues(spoken, [0, 2], [0, 2], words, segs, 5.0).status == ts.FAIL
    # overlap, and an unassigned gap
    overlap = ts.evaluate_cues(
        spoken, [0, 2], [0, 2], words, [(0, 1.5), (1.0, 1.0)], 2.0
    )
    assert overlap.status == ts.FAIL
    gap = ts.evaluate_cues(spoken, [0, 2], [0, 2], words, [(0, 1.0), (4.0, 1.0)], 5.0)
    assert gap.status == ts.FAIL
    # the pause between the last word of a segment and the next onset is fine
    pause = ts.evaluate_cues(spoken, [0, 2], [0, 2], words, [(0, 0.9), (1.0, 1.0)], 2.0)
    assert pause.status == ts.PASS
    # wrong segment count
    short = ts.evaluate_cues(spoken, [0, 2], [0, 2], words, segs[:1], 1.0)
    assert short.status == ts.FAIL
    # zero length segment
    zero = ts.evaluate_cues(
        spoken, [0, 2], [0, 2], words, [(0.0, 0.0), (0.0, 2.0)], 2.0
    )
    assert zero.status == ts.FAIL


# ---------------------------------------------------------------------------
# summary, exit code, helpers
# ---------------------------------------------------------------------------


def test_exit_code_only_fail_counts():
    C = ts.Check
    assert ts.exit_code([C("a", ts.PASS), C("b", ts.WARN), C("c", ts.SKIP)]) == 0
    assert ts.exit_code([C("a", ts.PASS), C("b", ts.FAIL)]) == 1
    assert ts.exit_code([]) == 0


def test_summary_text_and_counts():
    C = ts.Check
    text = ts.format_summary([C("narration", ts.FAIL, "boom"), C("x", ts.PASS)])
    assert "1 passed, 0 warnings, 1 failed, 0 skipped" in text
    assert "NOT verified" in text
    assert "narration works" in ts.format_summary([C("x", ts.PASS)])
    assert text.isascii()


def test_format_check_shows_hint_only_on_fail_or_warn():
    c = ts.Check("n", ts.FAIL, "d", "do this")
    assert "fix: do this" in ts.format_check(c)
    assert "fix:" not in ts.format_check(ts.Check("n", ts.PASS, "d", "do this"))


def test_mask_proxy_and_network_hint():
    assert ts.mask_proxy("http://user:pw@proxy:8080") == "http://***@proxy:8080"
    assert ts.mask_proxy(None) == ""
    assert "proxy" in ts.network_hint("Cannot connect to host x ssl:True")
    assert "Unexpected" in ts.network_hint("KeyError: 'foo'")


def test_survey_check_warns_on_count_difference():
    assert ts.survey_check("l", ["a", "b"], ["a", "b"]).status == ts.PASS
    assert ts.survey_check("l", ["a-b"], ["a", "b"]).status == ts.WARN


def test_survey_samples_match_the_tokenization_test():
    from tests.test_cue_tokenization import NARRATION_SAMPLES

    assert ts.SURVEY_SAMPLES == NARRATION_SAMPLES


def test_narration_has_contraction_number_and_hyphen():
    text = ts.NARRATION_WITH_CUES
    assert "Don't" in text and "16" in text and "well-known" in text
    assert text.count("[CUE]") == 2


# ---------------------------------------------------------------------------
# mocked end-to-end runs
# ---------------------------------------------------------------------------


def _args(tmp_path, no_survey=True):
    return argparse.Namespace(out_dir=str(tmp_path), no_survey=no_survey)


def _patch_ok(monkeypatch, split_hyphens=False, scale=1.0):
    def fake_narrate(text, path):
        with open(path, "wb") as f:
            f.write(b"x" * 100)
        return _fake_timestamps(text, split_hyphens=split_hyphens)

    def fake_probe(path):
        n = len(ts.NARRATION_WITH_CUES.replace("[CUE]", "").split())
        return {"has_audio": True, "codec": "mp3", "duration": (0.4 * n + 0.4) * scale}

    monkeypatch.setattr(ts, "narrate", fake_narrate)
    monkeypatch.setattr(ts, "probe_audio", fake_probe)
    monkeypatch.setattr(ts.shutil, "which", lambda name: "/usr/bin/" + name)


def test_run_all_passes_with_mocked_service(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    checks = ts.run_all(_args(tmp_path), emit=lambda *_: None)
    assert [c.name for c in checks if c.status == ts.FAIL] == []
    assert {c.name for c in checks} >= {
        "narration",
        "duration plausible",
        "word timestamps",
        "timestamps monotonic",
        "cue mapping",
    }
    assert ts.exit_code(checks) == 0


def test_run_all_hyphen_split_is_warn_and_cues_still_map(tmp_path, monkeypatch):
    _patch_ok(monkeypatch, split_hyphens=True)
    checks = {c.name: c for c in ts.run_all(_args(tmp_path), emit=lambda *_: None)}
    assert checks["word timestamps"].status == ts.WARN
    assert checks["cue mapping"].status == ts.PASS
    assert ts.exit_code(list(checks.values())) == 0


def test_run_all_implausible_duration_fails(tmp_path, monkeypatch):
    _patch_ok(monkeypatch, scale=10.0)
    checks = ts.run_all(_args(tmp_path), emit=lambda *_: None)
    assert ts.exit_code(checks) == 1


def test_run_all_network_failure_is_clean_fail(tmp_path, monkeypatch):
    def boom(text, path):
        raise RuntimeError("edge-tts failed after 3 attempts: Cannot connect to host")

    monkeypatch.setattr(ts, "narrate", boom)
    monkeypatch.setattr(ts.shutil, "which", lambda name: "/usr/bin/" + name)
    checks = ts.run_all(_args(tmp_path, no_survey=False), emit=lambda *_: None)
    by_name = {c.name: c for c in checks}
    assert by_name["narration"].status == ts.FAIL
    assert "Cannot connect" in by_name["narration"].detail
    assert "proxy" in by_name["narration"].hint
    assert by_name["tokenization survey"].status == ts.SKIP
    assert ts.exit_code(checks) == 1


def test_run_all_survey_runs_and_reports(tmp_path, monkeypatch):
    _patch_ok(monkeypatch, split_hyphens=True)
    lines = []
    checks = ts.run_all(_args(tmp_path, no_survey=False), emit=lines.append)
    survey = {c.name: c for c in checks if c.name.startswith("tokens:")}
    assert len(survey) == len(ts.SURVEY_SAMPLES)
    assert survey["tokens: hyphenated word"].status == ts.WARN
    assert survey["tokens: simple sentence"].status == ts.PASS
    assert any("boundaries:" in ln for ln in lines)
    assert ts.exit_code(checks) == 0  # survey findings never fail the run


def test_missing_ffprobe_is_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(ts.shutil, "which", lambda name: None)
    checks = ts.run_all(_args(tmp_path), emit=lambda *_: None)
    assert ts.exit_code(checks) == 1
