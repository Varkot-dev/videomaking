"""
Tests for manimgen/renderer/assembler.py

Covers: section boundary detection, single-clip fast path, multi-clip
normalisation, hard-cut vs xfade strategy, empty input guard,
output path naming, intermediate file cleanup.

All subprocess calls are mocked — no real ffmpeg needed.
"""

import os
import pytest
from unittest.mock import patch, MagicMock, call

from manimgen.renderer.assembler import (
    assemble_video,
    _section_boundaries,
    _hard_concat,
    _xfade_pair,
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
        """_hard_concat uses the concat demuxer (-f concat), not filter_complex."""
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4", "c.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run:
            _hard_concat(clips, out)
        cmd = mock_run.call_args[0][0]
        assert "-f" in cmd
        assert "concat" in cmd

    def test_uses_stream_copy(self, tmp_path):
        """_hard_concat uses stream copy (-c copy) — no re-encoding."""
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run:
            _hard_concat(clips, out)
        cmd = mock_run.call_args[0][0]
        assert "-c" in cmd
        idx = cmd.index("-c")
        assert cmd[idx + 1] == "copy"

    def test_no_xfade_in_hard_concat(self, tmp_path):
        """_hard_concat must never use xfade — that's only for section boundaries."""
        clips = _make_clips(tmp_path, ["a.mp4", "b.mp4"])
        out = str(tmp_path / "merged.mp4")
        with patch("manimgen.renderer.assembler.subprocess.run",
                   return_value=_ok()) as mock_run:
            _hard_concat(clips, out)
        # Check flags only — not path strings which could contain "xfade" as a substring
        args = mock_run.call_args[0][0]
        flags = [a for a in args if a.startswith("-")]
        assert not any("xfade" in f for f in flags)


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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

class TestConstants:

    def test_xfade_duration_is_reasonable(self):
        assert 0.1 <= _XFADE_DURATION <= 1.0


# ---------------------------------------------------------------------------
# _normalise_all — preset consistency (Issue 5)
# ---------------------------------------------------------------------------

class TestNormaliseAllPreset:

    def test_with_audio_uses_slow_preset(self, tmp_path):
        """_normalise_all (with-audio branch) must use -preset slow to match _xfade_pair."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake")

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            return _ok()

        with patch("manimgen.renderer.assembler._has_audio_stream", return_value=True), \
             patch("subprocess.run", side_effect=fake_run):
            from manimgen.renderer.assembler import _normalise_all
            _normalise_all([str(clip)], str(tmp_path))

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        assert "-preset" in cmd
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "slow", (
            f"Expected '-preset slow' but got '-preset {cmd[idx + 1]}'. "
            "_normalise_all must match _xfade_pair quality setting."
        )

    def test_no_audio_uses_slow_preset(self, tmp_path):
        """_normalise_all (no-audio branch) must also use -preset slow."""
        clip = tmp_path / "clip.mp4"
        clip.write_bytes(b"fake")

        captured_cmds = []

        def fake_run(cmd, **kwargs):
            captured_cmds.append(list(cmd))
            return _ok()

        with patch("manimgen.renderer.assembler._has_audio_stream", return_value=False), \
             patch("manimgen.renderer.assembler._video_duration", return_value=5.0), \
             patch("subprocess.run", side_effect=fake_run):
            from manimgen.renderer.assembler import _normalise_all
            _normalise_all([str(clip)], str(tmp_path))

        assert len(captured_cmds) == 1
        cmd = captured_cmds[0]
        assert "-preset" in cmd
        idx = cmd.index("-preset")
        assert cmd[idx + 1] == "slow", (
            f"Expected '-preset slow' in no-audio branch but got '-preset {cmd[idx + 1]}'."
        )
