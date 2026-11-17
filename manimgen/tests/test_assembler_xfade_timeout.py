"""Crossfades: one encode for the whole video, with safe fallbacks."""

import subprocess

import pytest

from manimgen.renderer import assembler

pytestmark = pytest.mark.unit


def _timeout(*args, **kwargs):
    raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=300)


def test_all_boundaries_are_crossfaded_in_a_single_ffmpeg_run(tmp_path, monkeypatch):
    runs = []
    monkeypatch.setattr(assembler, "_video_duration", lambda p: 10.0)
    monkeypatch.setattr(
        assembler.subprocess, "run", lambda cmd, **kw: runs.append((cmd, kw))
    )

    result = assembler._concat(["a.mp4", "b.mp4", "c.mp4"], {1, 2}, str(tmp_path))

    assert len(runs) == 1  # one encode, not one per boundary
    cmd, kwargs = runs[0]
    assert cmd.count("-i") == 3
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert graph.count("xfade=") == 2 and graph.count("acrossfade=") == 2
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "48000"
    assert kwargs["timeout"] >= 300
    assert result.endswith("_xfade_all.mp4")


def test_chain_offsets_account_for_earlier_fades():
    d = assembler._XFADE_DURATION
    plan = assembler._xfade_chain_plan([10.0, 5.0, 8.0])
    assert plan[0] == (d, pytest.approx(10.0 - d))
    # After the first fade the joined video is 10 + 5 - d long.
    assert plan[1] == (d, pytest.approx(15.0 - 2 * d))


def test_a_very_short_section_is_not_dissolved_away():
    ((d, offset),) = assembler._xfade_chain_plan([0.1, 5.0])
    assert d == pytest.approx(0.05)
    assert offset > 0


def test_single_pass_failure_falls_back_to_pairwise(tmp_path, monkeypatch):
    pairs = []
    monkeypatch.setattr(assembler, "_xfade_chain", _timeout)
    monkeypatch.setattr(
        assembler, "_xfade_pair", lambda a, b, out: pairs.append((a, b))
    )

    result = assembler._concat(["a.mp4", "b.mp4"], {1}, str(tmp_path))

    assert pairs == [("a.mp4", "b.mp4")]
    assert result.endswith("_xfade_001.mp4")


def test_xfade_timeout_falls_back_to_hard_concat(tmp_path, monkeypatch):
    calls = []

    def hard_concat(paths, out):
        calls.append(list(paths))
        open(out, "wb").close()

    monkeypatch.setattr(assembler, "_xfade_chain", _timeout)
    monkeypatch.setattr(assembler, "_xfade_pair", _timeout)
    monkeypatch.setattr(assembler, "_hard_concat", hard_concat)

    result = assembler._concat(["a.mp4", "b.mp4"], {1}, str(tmp_path))

    assert calls == [["a.mp4", "b.mp4"]]
    assert result.endswith("_xfade_001.mp4")
