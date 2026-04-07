"""
Tests for the rewritten manimgen/renderer/muxer.py

Key invariants to enforce:
  - NEVER speed-warp: setpts must never appear in any ffmpeg command
  - NEVER loop: stream_loop must never appear
  - Audio longer → tpad (freeze last video frame)
  - Video longer → apad (pad audio with silence)
  - Equal durations → apad path (video >= audio branch)
  - Large mismatch (>0.5s) logs a warning
  - Small mismatch (<0.5s) produces no warning
  - ffprobe failure → RuntimeError with install hint
  - ffmpeg failure → RuntimeError with output path
  - Output directory is created if missing
"""

import logging
import pytest
from unittest.mock import patch, MagicMock, call

from manimgen.renderer.muxer import (
    mux_audio_video,
    _get_duration,
    _WARN_THRESHOLD_SECONDS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_subprocess(video_dur: float, audio_dur: float):
    """Context manager: patches _get_duration and subprocess.run.

    _get_duration is called:
      1. video_dur  (top-level, before branching)
      2. audio_dur  (top-level, before branching)
      3. video_dur  (inside _mux_freeze_video, to compute the pad gap)
    """
    calls_made = []

    def fake_run(cmd, **kwargs):
        calls_made.append(cmd)
        m = MagicMock()
        m.returncode = 0
        m.stderr = ""
        return m

    return (
        patch("manimgen.renderer.muxer._get_duration",
              side_effect=[video_dur, audio_dur, video_dur]),
        patch("manimgen.renderer.muxer.subprocess.run", side_effect=fake_run),
        calls_made,
    )


def _run_mux(video_dur, audio_dur, tmp_path):
    """Run mux_audio_video with mocked durations; return the ffmpeg command."""
    p1, p2, calls = _mock_subprocess(video_dur, audio_dur)
    with p1, p2, patch("os.makedirs"):
        mux_audio_video("video.mp4", "audio.mp3", str(tmp_path / "out.mp4"))
    # calls[0] is the ffmpeg command (subprocess.run)
    return " ".join(calls[0]) if calls else ""


# ---------------------------------------------------------------------------
# No speed-warping — ever
# ---------------------------------------------------------------------------

def _filter_complex_value(cmd_str: str) -> str:
    """Extract just the value passed to -filter_complex from a command string."""
    parts = cmd_str.split()
    for i, p in enumerate(parts):
        if p == "-filter_complex" and i + 1 < len(parts):
            return parts[i + 1]
    return ""


class TestNoSpeedWarp:

    def test_setpts_never_used_when_audio_longer(self, tmp_path):
        cmd = _run_mux(video_dur=5.0, audio_dur=10.0, tmp_path=tmp_path)
        assert "setpts" not in _filter_complex_value(cmd)

    def test_setpts_never_used_when_video_longer(self, tmp_path):
        cmd = _run_mux(video_dur=10.0, audio_dur=5.0, tmp_path=tmp_path)
        assert "setpts" not in _filter_complex_value(cmd)

    def test_setpts_never_used_when_equal(self, tmp_path):
        cmd = _run_mux(video_dur=10.0, audio_dur=10.0, tmp_path=tmp_path)
        assert "setpts" not in _filter_complex_value(cmd)

    def test_stream_loop_never_used(self, tmp_path):
        # stream_loop is a top-level flag, not in filter_complex — check full cmd args
        cmd = _run_mux(video_dur=3.0, audio_dur=30.0, tmp_path=tmp_path)
        # Split on spaces and check flag tokens specifically (not path components)
        tokens = cmd.split()
        assert "-stream_loop" not in tokens


# ---------------------------------------------------------------------------
# Correct strategy selection
# ---------------------------------------------------------------------------

class TestStrategySelection:

    def test_audio_longer_uses_tpad_freeze(self, tmp_path):
        """Audio longer than video → freeze last video frame."""
        cmd = _run_mux(video_dur=5.0, audio_dur=8.0, tmp_path=tmp_path)
        assert "tpad" in cmd
        assert "apad" not in cmd

    def test_video_longer_uses_apad_silence(self, tmp_path):
        """Video longer than audio → pad audio with silence."""
        cmd = _run_mux(video_dur=10.0, audio_dur=7.0, tmp_path=tmp_path)
        assert "apad" in cmd
        assert "tpad" not in cmd

    def test_equal_duration_uses_apad(self, tmp_path):
        """Equal → falls into video >= audio branch → apad."""
        cmd = _run_mux(video_dur=10.0, audio_dur=10.0, tmp_path=tmp_path)
        assert "apad" in cmd

    def test_tpad_uses_stop_mode_clone(self, tmp_path):
        """Freeze must clone the last frame, not produce black."""
        cmd = _run_mux(video_dur=5.0, audio_dur=8.0, tmp_path=tmp_path)
        assert "stop_mode=clone" in cmd

    def test_apad_whole_dur_set_to_video_duration(self, tmp_path):
        """apad whole_dur must match video duration exactly."""
        cmd = _run_mux(video_dur=10.0, audio_dur=7.0, tmp_path=tmp_path)
        assert "whole_dur=10.000000" in cmd

    def test_tpad_stop_duration_set_to_gap(self, tmp_path):
        """tpad stop_duration must equal the gap (audio - video), not the total audio duration.
        Padding by the gap brings video up to audio length exactly."""
        cmd = _run_mux(video_dur=5.0, audio_dur=8.0, tmp_path=tmp_path)
        assert "stop_duration=3.000000" in cmd

    def test_audio_longer_by_small_amount_still_uses_tpad(self, tmp_path):
        """Even a 50ms mismatch should use tpad, not apad."""
        cmd = _run_mux(video_dur=5.0, audio_dur=5.05, tmp_path=tmp_path)
        assert "tpad" in cmd

    def test_video_longer_by_small_amount_uses_apad(self, tmp_path):
        cmd = _run_mux(video_dur=5.05, audio_dur=5.0, tmp_path=tmp_path)
        assert "apad" in cmd


# ---------------------------------------------------------------------------
# Output path and encoding
# ---------------------------------------------------------------------------

class TestOutputEncoding:

    def test_output_path_in_command(self, tmp_path):
        out = str(tmp_path / "out.mp4")
        p1, p2, calls = _mock_subprocess(10.0, 8.0)
        with p1, p2, patch("os.makedirs"):
            mux_audio_video("v.mp4", "a.mp3", out)
        assert out in " ".join(calls[0])

    def test_aac_always_used_for_audio(self, tmp_path):
        for vd, ad in [(10, 8), (8, 10), (10, 10)]:
            cmd = _run_mux(video_dur=vd, audio_dur=ad, tmp_path=tmp_path)
            assert "aac" in cmd

    def test_libx264_used_when_video_re_encoded(self, tmp_path):
        """tpad path must re-encode video (can't stream-copy while padding frames)."""
        cmd = _run_mux(video_dur=5.0, audio_dur=8.0, tmp_path=tmp_path)
        assert "libx264" in cmd

    def test_video_copy_used_in_apad_path(self, tmp_path):
        """apad only touches audio — video can be stream-copied."""
        cmd = _run_mux(video_dur=10.0, audio_dur=7.0, tmp_path=tmp_path)
        # "-c:v copy" must appear (not libx264)
        assert "-c:v copy" in cmd or ("copy" in cmd and "libx264" not in cmd)

    def test_output_directory_created(self, tmp_path):
        out = str(tmp_path / "subdir" / "out.mp4")
        p1, p2, _ = _mock_subprocess(10.0, 8.0)
        with p1, p2:
            mux_audio_video("v.mp4", "a.mp3", out)
        assert (tmp_path / "subdir").exists()


# ---------------------------------------------------------------------------
# Warnings
# ---------------------------------------------------------------------------

class TestWarnings:

    def test_warns_on_large_mismatch(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="manimgen.renderer.muxer"):
            _run_mux(video_dur=5.0, audio_dur=10.0, tmp_path=tmp_path)
        assert any("mismatch" in r.message.lower() for r in caplog.records)

    def test_no_warning_on_small_mismatch(self, tmp_path, caplog):
        # 0.1s diff — well under the 0.5s threshold
        with caplog.at_level(logging.WARNING, logger="manimgen.renderer.muxer"):
            _run_mux(video_dur=10.0, audio_dur=10.1, tmp_path=tmp_path)
        assert not any("mismatch" in r.message.lower() for r in caplog.records)

    def test_warning_threshold_constant_is_correct(self):
        assert _WARN_THRESHOLD_SECONDS == 0.5

    def test_warning_includes_both_durations(self, tmp_path, caplog):
        with caplog.at_level(logging.WARNING, logger="manimgen.renderer.muxer"):
            _run_mux(video_dur=5.0, audio_dur=10.0, tmp_path=tmp_path)
        msg = " ".join(r.message for r in caplog.records)
        assert "5" in msg and "10" in msg


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:

    def test_ffprobe_missing_raises_runtime_error(self, tmp_path):
        with patch("manimgen.renderer.muxer.subprocess.run",
                   side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="ffprobe not found"):
                mux_audio_video("v.mp4", "a.mp3", str(tmp_path / "out.mp4"))

    def test_ffmpeg_failure_raises_runtime_error(self, tmp_path):
        fail = MagicMock()
        fail.returncode = 1
        fail.stderr = "codec error"

        with patch("manimgen.renderer.muxer._get_duration",
                   side_effect=[10.0, 7.0]), \
             patch("manimgen.renderer.muxer.subprocess.run",
                   return_value=fail), \
             patch("os.makedirs"):
            with pytest.raises(RuntimeError, match="ffmpeg failed"):
                mux_audio_video("v.mp4", "a.mp3", str(tmp_path / "out.mp4"))

    def test_returns_output_path_on_success(self, tmp_path):
        out = str(tmp_path / "out.mp4")
        p1, p2, _ = _mock_subprocess(10.0, 8.0)
        with p1, p2, patch("os.makedirs"):
            result = mux_audio_video("v.mp4", "a.mp3", out)
        assert result == out
