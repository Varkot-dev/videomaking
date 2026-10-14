"""
Tests for manimgen/renderer/assembler.py

Covers: section boundary detection, single-clip fast path, multi-clip
normalisation, hard-cut vs xfade strategy, empty input guard,
output path naming, intermediate file cleanup.

All subprocess calls are mocked — no real ffmpeg needed.
"""

import os
import subprocess
import pytest
from unittest.mock import patch, MagicMock

from manimgen.renderer.assembler import (
    assemble_video,
    _section_boundaries,
    _hard_concat,
    _crossfade_sections,
    _xfade_chain,
    _xfade_pair,
    _xfade_timeout,
    _video_duration,
    _has_audio_stream,
    _XFADE_DURATION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ok():
    m = MagicMock()
    m.returncode = 0
    m.stdout = "5.0\n"
    m.stderr = ""
    return m


def _make_clips(tmp_path, names: list[str]) -> list[str]:
    """Create empty placeholder files and return their paths."""
    paths = []
    for name in names:
        p = tmp_path / name
        p.write_bytes(b"fake")
        paths.append(str(p))
    return paths


# ---------------------------------------------------------------------------
# _section_boundaries
# ---------------------------------------------------------------------------

class TestSectionBoundaries:

    def test_index_zero_always_a_boundary(self, tmp_path):
        clips = _make_clips(tmp_path, [
            "section_01_cue00.mp4",
            "section_01_cue01.mp4",
        ])
        b = _section_boundaries(clips)
        assert 0 in b

    def test_cue00_clips_are_boundaries(self, tmp_path):
        clips = _make_clips(tmp_path, [
            "section_01_cue00.mp4",
            "section_01_cue01.mp4",
            "section_01_cue02.mp4",
            "section_02_cue00.mp4",
            "section_02_cue01.mp4",
        ])
        b = _section_boundaries(clips)
        assert 0 in b   # section_01_cue00
        assert 3 in b   # section_02_cue00
        assert 1 not in b
        assert 2 not in b
        assert 4 not in b

    def test_non_cue_clips_are_each_their_own_boundary(self, tmp_path):
        clips = _make_clips(tmp_path, [
            "section_01.mp4",
            "section_02.mp4",
        ])
        b = _section_boundaries(clips)
        assert 0 in b
        assert 1 in b

    def test_mixed_cue_and_legacy_clips(self, tmp_path):
        clips = _make_clips(tmp_path, [
            "section_01_cue00.mp4",
            "section_01_cue01.mp4",
            "section_02.mp4",          # legacy non-cue
            "section_03_cue00.mp4",
        ])
        b = _section_boundaries(clips)
        assert 0 in b   # first cue00
        assert 2 in b   # legacy
        assert 3 in b   # next cue00
        assert 1 not in b


# ---------------------------------------------------------------------------
# assemble_video — guards
# ---------------------------------------------------------------------------

class TestAssembleGuards:

    def test_empty_list_raises(self, tmp_path):
        with pytest.raises(ValueError, match="no clips"):
            assemble_video([], "My Topic")

    def test_single_clip_uses_os_replace(self, tmp_path):
        clip = tmp_path / "section_01_cue00.mp4"
        clip.write_bytes(b"fake")
        with patch("manimgen.renderer.assembler.os.replace") as mock_replace, \
             patch("os.makedirs"):
            assemble_video([str(clip)], "My Topic")
        mock_replace.assert_called_once()


# ---------------------------------------------------------------------------
# assemble_video — output naming
# ---------------------------------------------------------------------------

class TestOutputNaming:

    def test_spaces_replaced_with_underscores(self, tmp_path):
        clips = _make_clips(tmp_path, ["section_01_cue00.mp4"])
        with patch("manimgen.renderer.assembler.subprocess.run", return_value=_ok()), \
             patch("manimgen.renderer.assembler.os.replace") as mock_replace, \
             patch("os.makedirs"):
            assemble_video(clips, "Binary Search")
        out_path = mock_replace.call_args[0][1]
        assert "binary_search" in out_path

    def test_slashes_replaced_with_dashes(self, tmp_path):
        clips = _make_clips(tmp_path, ["section_01_cue00.mp4"])
        with patch("manimgen.renderer.assembler.subprocess.run", return_value=_ok()), \
             patch("manimgen.renderer.assembler.os.replace") as mock_replace, \
             patch("os.makedirs"):
            assemble_video(clips, "A/B Test")
        out_path = mock_replace.call_args[0][1]
        assert "/" not in os.path.basename(out_path)

    def test_output_ends_with_mp4(self, tmp_path):
        clips = _make_clips(tmp_path, ["section_01_cue00.mp4"])
        with patch("manimgen.renderer.assembler.subprocess.run", return_value=_ok()), \
             patch("manimgen.renderer.assembler.os.replace") as mock_replace, \
             patch("os.makedirs"):
            assemble_video(clips, "Test Topic")
        out_path = mock_replace.call_args[0][1]
        assert out_path.endswith(".mp4")


# ---------------------------------------------------------------------------
# _section_boundaries — cue naming edge cases
# ---------------------------------------------------------------------------

class TestBoundaryEdgeCases:

    def test_cue01_is_not_a_boundary(self, tmp_path):
        clips = _make_clips(tmp_path, [
            "section_01_cue00.mp4",
            "section_01_cue01.mp4",
            "section_01_cue02.mp4",
        ])
        b = _section_boundaries(clips)
        assert b == {0}

    def test_all_cue00_all_are_boundaries(self, tmp_path):
        clips = _make_clips(tmp_path, [
            "section_01_cue00.mp4",
            "section_02_cue00.mp4",
            "section_03_cue00.mp4",
        ])
        b = _section_boundaries(clips)
        assert b == {0, 1, 2}


# ---------------------------------------------------------------------------
# _hard_concat
# ---------------------------------------------------------------------------

class TestHardConcat:

    def test_ffmpeg_called_with_concat_demuxer(self, tmp_path):
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4", "c.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run:
            _hard_concat(clips, out)
        cmd = " ".join(mock_run.call_args[0][0])
        assert "-f" in cmd
        assert "concat" in cmd

    def test_all_inputs_in_list_file(self, tmp_path):
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()):
            _hard_concat(clips, out)
        # The list file is cleaned up after the call, but we can verify ffmpeg
        # received a concat list file path as input
        # (clips are written to the list file, not passed directly on the command line)

    def test_no_xfade_in_hard_concat(self, tmp_path):
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run:
            _hard_concat(clips, out)
        # Check flags only (not file paths, which may contain substrings like "xfade")
        args = mock_run.call_args[0][0]
        flags = [a for a in args if a.startswith("-")]
        assert not any("xfade" in f for f in flags)
        assert "-f" in flags
        assert "concat" in args

    def test_uses_stream_copy(self, tmp_path):
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run:
            _hard_concat(clips, out)
        cmd = " ".join(mock_run.call_args[0][0])
        assert "-c copy" in cmd or ("-c" in cmd and "copy" in cmd)


# ---------------------------------------------------------------------------
# _xfade_pair
# ---------------------------------------------------------------------------

class TestXfadePair:

    def test_xfade_transition_in_command(self, tmp_path):
        a, b = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "out.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration", return_value=5.0):
            _xfade_pair(a, b, out)
        cmd = " ".join(mock_run.call_args[0][0])
        assert "xfade" in cmd
        assert "acrossfade" in cmd

    def test_xfade_uses_configured_duration(self, tmp_path):
        a, b = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "out.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration", return_value=5.0):
            _xfade_pair(a, b, out)
        cmd = " ".join(mock_run.call_args[0][0])
        assert str(_XFADE_DURATION) in cmd

    def test_offset_is_duration_minus_fade(self, tmp_path):
        """offset = video_dur - xfade_duration."""
        a, b = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "out.mp4")
        video_dur = 5.0
        expected_offset = video_dur - _XFADE_DURATION
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration",
                   return_value=video_dur):
            _xfade_pair(a, b, out)
        cmd = " ".join(mock_run.call_args[0][0])
        assert f"offset={expected_offset}" in cmd

    def test_tiny_first_clip_does_not_offset_zero(self, tmp_path):
        """Regression: a 0.1s floor first clip must NOT produce offset=0,
        which would cross-dissolve the entire first section from frame one."""
        a, b = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "out.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration",
                   return_value=0.1):
            _xfade_pair(a, b, out)
        # The filtergraph is the second element of the ffmpeg command list.
        cmd_list = mock_run.call_args[0][0]
        filtergraph = cmd_list[cmd_list.index("-filter_complex") + 1]
        # Parse the actual offset value: it must be strictly positive, never 0,
        # which would cross-dissolve the entire first section from frame one.
        offset_str = filtergraph.split("offset=")[1].split("[")[0]
        assert float(offset_str) > 0.0, (
            f"offset must stay positive for a 0.1s clip, got {offset_str}"
        )
        # The transition shrinks to half the clip (0.1 - 0.1/2 = 0.05).
        assert abs(float(offset_str) - 0.05) < 1e-6


class TestHasAudioStream:
    """Regression: probe failure must fail safe to False so the silent-audio
    injection path runs (always valid), rather than claiming audio is present
    and producing an ffmpeg `-c:a aac` map that aborts on a video-only clip."""

    def test_probe_failure_returns_false(self, tmp_path):
        a = _make_clips(tmp_path, ["a.mp4"])[0]
        with patch("manimgen.renderer.assembler.subprocess.Popen",
                   side_effect=OSError("ffprobe blew up")):
            assert _has_audio_stream(a) is False


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:

    def test_xfade_duration_is_reasonable(self):
        assert 0.1 <= _XFADE_DURATION <= 1.0


class TestVideoDurationRobustness:
    """Regression: ffprobe returns 'N/A' for some re-encoded clips with no
    format-level duration. float('N/A') used to crash the ENTIRE assembly,
    destroying an otherwise-good render at the last step."""

    def _result(self, stdout: str):
        m = MagicMock()
        m.returncode = 0
        m.stdout = stdout
        m.stderr = ""
        return m

    def test_valid_format_duration(self):
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=self._result("7.5\n")):
            assert _video_duration("/fake.mp4") == pytest.approx(7.5)

    def test_na_format_duration_does_not_crash_falls_to_floor(self):
        # format=duration is 'N/A' AND stream=duration is 'N/A' →
        # must return the 0.1s floor, NOT raise ValueError.
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=self._result("N/A\n")):
            dur = _video_duration("/fake.mp4")
        assert dur == pytest.approx(0.1)

    def test_na_format_falls_back_to_stream_duration(self):
        # First probe (format) → N/A; second probe (stream) → real value.
        calls = [self._result("N/A\n"), self._result("4.2\n")]
        with patch("manimgen.renderer.assembler.subprocess.run",
                   side_effect=calls):
            assert _video_duration("/fake.mp4") == pytest.approx(4.2)

    def test_empty_output_does_not_crash(self):
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=self._result("\n")):
            assert _video_duration("/fake.mp4") == pytest.approx(0.1)


# ---------------------------------------------------------------------------
# Section crossfades: single pass, scaled timeout, degrade on failure (#75)
# ---------------------------------------------------------------------------


def _section_clips(tmp_path, n):
    return _make_clips(tmp_path, [f"section_{i:02d}.mp4" for i in range(1, n + 1)])


def _graph(cmd):
    return cmd[cmd.index("-filter_complex") + 1]


class TestCrossfadeSinglePass:

    def test_all_boundaries_in_one_ffmpeg_call(self, tmp_path):
        clips = _section_clips(tmp_path, 4)
        out = str(tmp_path / "out.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration", return_value=5.0):
            _crossfade_sections(clips, out, str(tmp_path))
        assert mock_run.call_count == 1
        cmd = mock_run.call_args[0][0]
        assert _graph(cmd).count("xfade=") == 3
        assert _graph(cmd).count("acrossfade=") == 3
        assert cmd.count("-i") == 4
        # Encode contract: same settings as every other assembler encode.
        assert cmd[cmd.index("-preset") + 1] == "slow"
        assert cmd[cmd.index("-crf") + 1] == "17"
        assert cmd[cmd.index("-ar") + 1] == "48000"

    def test_offsets_accumulate_minus_each_fade(self, tmp_path):
        clips = _section_clips(tmp_path, 3)
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration",
                   side_effect=[5.0, 4.0, 6.0]):
            _xfade_chain(clips, str(tmp_path / "out.mp4"))
        graph = _graph(mock_run.call_args[0][0])
        offsets = [float(x.split("[")[0]) for x in graph.split("offset=")[1:]]
        d = _XFADE_DURATION
        assert offsets == pytest.approx([5.0 - d, 5.0 + 4.0 - 2 * d])

    def test_timeout_scales_with_output_duration(self, tmp_path):
        clips = _section_clips(tmp_path, 8)
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration", return_value=45.0):
            _xfade_chain(clips, str(tmp_path / "out.mp4"))
        timeout = mock_run.call_args.kwargs["timeout"]
        total = 8 * 45.0 - 7 * _XFADE_DURATION
        assert timeout == pytest.approx(_xfade_timeout(total))
        assert timeout > 300

    def test_tiny_section_shrinks_its_fade(self, tmp_path):
        clips = _section_clips(tmp_path, 2)
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run, \
             patch("manimgen.renderer.assembler._video_duration",
                   side_effect=[0.1, 5.0]):
            _xfade_chain(clips, str(tmp_path / "out.mp4"))
        graph = _graph(mock_run.call_args[0][0])
        offset = float(graph.split("offset=")[1].split("[")[0])
        assert offset == pytest.approx(0.05)


class TestCrossfadeFallbacks:

    def _run(self, tmp_path, effect):
        clips = _section_clips(tmp_path, 3)
        out = str(tmp_path / "out.mp4")
        calls = []

        def fake_run(cmd, *args, **kwargs):
            calls.append(cmd)
            return effect(cmd)

        with patch("manimgen.renderer.assembler.subprocess.run",
                   side_effect=fake_run), \
             patch("manimgen.renderer.assembler._video_duration", return_value=5.0), \
             patch("manimgen.renderer.assembler.replace_with_retry"):
            _crossfade_sections(clips, out, str(tmp_path))
        return calls

    @staticmethod
    def _is_xfade(cmd):
        return "-filter_complex" in cmd

    @staticmethod
    def _is_concat(cmd):
        return "concat" in cmd and "copy" in cmd

    def test_timeout_falls_back_to_hard_concat(self, tmp_path, caplog):
        def effect(cmd):
            if self._is_xfade(cmd):
                raise subprocess.TimeoutExpired(cmd, 300)
            return _ok()

        with caplog.at_level("WARNING", logger="manimgen.renderer.assembler"):
            calls = self._run(tmp_path, effect)
        # One crossfade attempt, then a stream-copy concat; no pairwise retry.
        assert sum(self._is_xfade(c) for c in calls) == 1
        assert self._is_concat(calls[-1])
        assert "timed out" in caplog.text

    def test_error_retries_pairwise_then_succeeds(self, tmp_path):
        def effect(cmd):
            if self._is_xfade(cmd) and cmd.count("-i") > 2:
                raise subprocess.CalledProcessError(1, cmd)
            return _ok()

        calls = self._run(tmp_path, effect)
        # One failed single-pass call, then one pairwise encode per boundary.
        assert sum(self._is_xfade(c) for c in calls) == 3
        assert not any(self._is_concat(c) for c in calls)

    def test_pairwise_timeout_uses_hard_cut_per_pair(self, tmp_path):
        def effect(cmd):
            if self._is_xfade(cmd) and cmd.count("-i") > 2:
                raise subprocess.CalledProcessError(1, cmd)
            if self._is_xfade(cmd):
                raise subprocess.TimeoutExpired(cmd, 300)
            return _ok()

        calls = self._run(tmp_path, effect)  # must not raise
        assert sum(self._is_concat(c) for c in calls) == 2

    def test_assemble_video_survives_crossfade_timeout(self, tmp_path):
        """End to end through assemble_video: a timeout no longer aborts."""
        clips = _section_clips(tmp_path, 3)

        def fake_run(cmd, *args, **kwargs):
            if "-filter_complex" in cmd:
                raise subprocess.TimeoutExpired(cmd, 300)
            return _ok()

        with patch("manimgen.renderer.assembler.subprocess.run",
                   side_effect=fake_run), \
             patch("manimgen.renderer.assembler._has_audio_stream",
                   return_value=True), \
             patch("manimgen.renderer.assembler._video_duration", return_value=5.0), \
             patch("manimgen.renderer.assembler.paths.videos_dir",
                   return_value=str(tmp_path / "videos")), \
             patch("manimgen.renderer.assembler.os.replace") as mock_replace:
            out = assemble_video(clips, "Timeout Topic")
        assert out.endswith("timeout_topic.mp4")
        mock_replace.assert_called_once()
