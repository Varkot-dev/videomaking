"""Regression tests for B1 — ffmpeg subprocess calls that could hang forever.

Five call sites had no ``timeout=`` argument, so a wedged ffmpeg would stall
the pipeline indefinitely with no output:

  * ``tts.check_audio_not_silent``      (runs per section in Phase 1, before
                                         any render — a hang here stalls the
                                         entire run before it produces anything)
  * ``audio_slicer._check_ffmpeg``      (availability probe)
  * ``audio_slicer._ffmpeg_slice``      (per-cue slice)
  * ``audio_slicer._ffmpeg_copy``       (single-segment whole-file re-encode)

Each test asserts (a) a timeout is actually passed, and (b) a
``subprocess.TimeoutExpired`` is surfaced as an explicit, actionable error
rather than propagating as an opaque crash.
"""

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from manimgen.renderer import audio_slicer, tts


def _ok_result(stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = 0
    m.stdout = ""
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# Timeouts are actually passed
# ---------------------------------------------------------------------------


def test_check_audio_not_silent_passes_a_timeout():
    """tts.check_audio_not_silent must not call ffmpeg unbounded."""
    with patch(
        "manimgen.renderer.tts.subprocess.run", return_value=_ok_result("M: -20.0")
    ) as run:
        with patch("manimgen.renderer.tts.get_audio_duration", return_value=3.0):
            tts.check_audio_not_silent("/tmp/a.mp3")

    assert run.call_count == 1
    timeout = run.call_args.kwargs.get("timeout")
    assert timeout is not None, "ffmpeg ebur128 call has no timeout — it can hang forever"
    assert timeout == tts._EBUR128_TIMEOUT_SECONDS


def test_check_ffmpeg_passes_a_timeout():
    """The ffmpeg availability probe must not hang."""
    with patch(
        "manimgen.renderer.audio_slicer.subprocess.run", return_value=_ok_result()
    ) as run:
        audio_slicer._check_ffmpeg()

    timeout = run.call_args.kwargs.get("timeout")
    assert timeout is not None, "_check_ffmpeg has no timeout — it can hang forever"
    assert timeout == audio_slicer._FFPROBE_TIMEOUT_SECONDS


def test_ffmpeg_slice_passes_a_timeout():
    """Per-cue slicing must not hang."""
    with patch(
        "manimgen.renderer.audio_slicer.subprocess.run", return_value=_ok_result()
    ) as run:
        audio_slicer._ffmpeg_slice("/tmp/in.mp3", "/tmp/out.m4a", start=0.0, end=2.0)

    timeout = run.call_args.kwargs.get("timeout")
    assert timeout is not None, "_ffmpeg_slice has no timeout — it can hang forever"
    assert timeout == audio_slicer._FFMPEG_TIMEOUT_SECONDS


def test_ffmpeg_copy_passes_a_timeout():
    """Whole-file re-encode must not hang."""
    with patch(
        "manimgen.renderer.audio_slicer.subprocess.run", return_value=_ok_result()
    ) as run:
        audio_slicer._ffmpeg_copy("/tmp/in.mp3", "/tmp/out.m4a")

    timeout = run.call_args.kwargs.get("timeout")
    assert timeout is not None, "_ffmpeg_copy has no timeout — it can hang forever"
    assert timeout == audio_slicer._FFMPEG_TIMEOUT_SECONDS


# ---------------------------------------------------------------------------
# TimeoutExpired is handled explicitly, not left to propagate opaquely
# ---------------------------------------------------------------------------


def test_check_audio_not_silent_handles_timeout_without_crashing():
    """A wedged ffmpeg must degrade to an 'unknown' report, not crash Phase 1.

    This runs before any render; raising here would abort the whole run with
    an opaque TimeoutExpired and no output at all.
    """
    with patch(
        "manimgen.renderer.tts.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
    ):
        report = tts.check_audio_not_silent("/tmp/a.mp3")

    # Degrades gracefully: does not claim the audio is silent, does not crash.
    assert report["ok"] is True
    assert report["timed_out"] is True


def test_ffmpeg_slice_timeout_raises_actionable_error():
    with patch(
        "manimgen.renderer.audio_slicer.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            audio_slicer._ffmpeg_slice("/tmp/in.mp3", "/tmp/o.m4a", start=0.0, end=1.0)


def test_ffmpeg_copy_timeout_raises_actionable_error():
    with patch(
        "manimgen.renderer.audio_slicer.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=120),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            audio_slicer._ffmpeg_copy("/tmp/in.mp3", "/tmp/o.m4a")


def test_check_ffmpeg_timeout_raises_actionable_error():
    with patch(
        "manimgen.renderer.audio_slicer.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=30),
    ):
        with pytest.raises(RuntimeError, match="timed out"):
            audio_slicer._check_ffmpeg()


# ---------------------------------------------------------------------------
# Timeout budgets are named constants sized to the work each call does
# ---------------------------------------------------------------------------


def test_timeout_budgets_are_named_constants_not_magic_numbers():
    # A trivial availability probe should be short.
    assert audio_slicer._FFPROBE_TIMEOUT_SECONDS <= 30
    # A single-slice transcode gets a real budget, but nowhere near a
    # whole-video concat (assembler/muxer use 300s).
    assert 30 < audio_slicer._FFMPEG_TIMEOUT_SECONDS <= 300
    # ebur128 scans the entire section narration; more than a probe, less
    # than a full-video operation.
    assert 30 < tts._EBUR128_TIMEOUT_SECONDS <= 300
