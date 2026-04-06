"""
Tests for manimgen/renderer/muxer.py

Tests mux strategy selection logic without running ffmpeg.
All subprocess calls are mocked.
"""

import pytest
from unittest.mock import patch, MagicMock

from manimgen.renderer.muxer import (
    mux_audio_video,
    _MISMATCH_WARN_THRESHOLD,
    _MISMATCH_LOOP_THRESHOLD,
)


def _mock_mux(video_dur: float, audio_dur: float):
    """Helper: run mux_audio_video with mocked durations and capture the ffmpeg command."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        result = MagicMock()
        result.returncode = 0
        return result

    with patch("manimgen.renderer.muxer._get_video_duration", return_value=video_dur), \
         patch("manimgen.renderer.tts.get_audio_duration", return_value=audio_dur), \
         patch("manimgen.renderer.muxer.subprocess.run", side_effect=fake_run), \
         patch("os.makedirs"):
        mux_audio_video("video.mp4", "audio.mp3", "/tmp/out.mp4")

    return calls


class TestMuxStrategy:

    def test_audio_longer_small_mismatch_uses_setpts(self):
        # audio 10% longer than video — speed up video
        calls = _mock_mux(video_dur=40.0, audio_dur=44.0)
        cmd = " ".join(calls[0])
        assert "setpts" in cmd
        assert "stream_loop" not in cmd

    def test_audio_much_longer_uses_loop(self):
        # audio > 2x video — loop video
        calls = _mock_mux(video_dur=6.0, audio_dur=30.0)
        cmd = " ".join(calls[0])
        assert "stream_loop" in cmd
        assert "setpts" not in cmd

    def test_video_longer_uses_apad(self):
        # video longer than audio — pad audio
        calls = _mock_mux(video_dur=40.0, audio_dur=30.0)
        cmd = " ".join(calls[0])
        assert "apad" in cmd

    def test_equal_duration_uses_apad(self):
        # equal — falls into video >= audio branch, use apad
        calls = _mock_mux(video_dur=30.0, audio_dur=30.0)
        cmd = " ".join(calls[0])
        assert "apad" in cmd

    def test_output_path_in_command(self):
        calls = _mock_mux(video_dur=30.0, audio_dur=25.0)
        assert "/tmp/out.mp4" in calls[0]

    def test_always_uses_libx264_and_aac(self):
        for vd, ad in [(30, 25), (6, 30), (25, 30)]:
            calls = _mock_mux(video_dur=vd, audio_dur=ad)
            cmd = " ".join(calls[0])
            assert "libx264" in cmd
            assert "aac" in cmd

    def test_loop_threshold_constant(self):
        assert _MISMATCH_LOOP_THRESHOLD == 2.0

    def test_mismatch_warn_threshold_constant(self):
        assert _MISMATCH_WARN_THRESHOLD == 0.30


class TestMuxWarnings:

    def test_warns_on_large_mismatch(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="manimgen.renderer.muxer"):
            with patch("manimgen.renderer.muxer._get_video_duration", return_value=6.0), \
                 patch("manimgen.renderer.tts.get_audio_duration", return_value=30.0), \
                 patch("manimgen.renderer.muxer.subprocess.run", return_value=MagicMock()), \
                 patch("os.makedirs"):
                mux_audio_video("video.mp4", "audio.mp3", "/tmp/out.mp4")
        assert any("mismatch" in r.message.lower() for r in caplog.records)

    def test_no_warning_on_small_mismatch(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="manimgen.renderer.muxer"):
            with patch("manimgen.renderer.muxer._get_video_duration", return_value=30.0), \
                 patch("manimgen.renderer.tts.get_audio_duration", return_value=31.0), \
                 patch("manimgen.renderer.muxer.subprocess.run", return_value=MagicMock()), \
                 patch("os.makedirs"):
                mux_audio_video("video.mp4", "audio.mp3", "/tmp/out.mp4")
        assert not any("mismatch" in r.message.lower() for r in caplog.records)
