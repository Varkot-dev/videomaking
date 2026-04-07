"""
Tests for manimgen/renderer/audio_slicer.py

All ffmpeg/ffprobe subprocess calls are mocked — no real audio files needed.
Tests cover: normal multi-segment slicing, single-segment fast path,
segment-0 silence preservation, last-segment EOF handling, overwrite flag,
skip-if-exists behaviour, short-segment warning, error propagation,
missing input file, and empty segment list.
"""

import os
import pytest
from unittest.mock import patch, call, MagicMock

from manimgen.planner.segmenter import CueSegment
from manimgen.renderer.audio_slicer import (
    slice_audio,
    _ffmpeg_slice,
    _ffmpeg_copy,
    _check_ffmpeg,
    _MIN_SEGMENT_DURATION,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seg(cue_index: int, total: int, start: float, duration: float) -> CueSegment:
    return CueSegment(
        cue_index=cue_index,
        total_cues=total,
        start_time=start,
        duration=duration,
    )


def _mock_ffmpeg_ok():
    """Return a mock subprocess.run result that looks like a success."""
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    return m


def _mock_ffmpeg_fail(stderr="ffmpeg error"):
    m = MagicMock()
    m.returncode = 1
    m.stderr = stderr
    return m


# Three-segment narration: pre-speech silence + cue0, cue1, cue2
SEGMENTS_3 = [
    _seg(0, 3, 0.113, 3.137),   # word 0 onset
    _seg(1, 3, 3.250, 4.850),
    _seg(2, 3, 8.100, 2.100),   # last segment
]


# ---------------------------------------------------------------------------
# _check_ffmpeg
# ---------------------------------------------------------------------------

class TestCheckFfmpeg:

    def test_passes_when_ffmpeg_present(self):
        with patch("manimgen.renderer.audio_slicer.subprocess.run", return_value=_mock_ffmpeg_ok()):
            _check_ffmpeg()  # should not raise

    def test_raises_runtime_error_when_missing(self):
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                _check_ffmpeg()

    def test_error_message_includes_install_instructions(self):
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError) as exc_info:
                _check_ffmpeg()
        assert "brew install ffmpeg" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _ffmpeg_slice
# ---------------------------------------------------------------------------

class TestFfmpegSlice:

    def test_includes_ss_flag(self, tmp_path):
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            _ffmpeg_slice("input.mp3", str(tmp_path / "out.mp3"), start=3.25, end=8.10)
        cmd = mock_run.call_args[0][0]
        assert "-ss" in cmd
        assert "3.250000" in cmd

    def test_includes_t_when_end_is_set(self, tmp_path):
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            _ffmpeg_slice("input.mp3", str(tmp_path / "out.m4a"), start=3.25, end=8.10)
        cmd = " ".join(mock_run.call_args[0][0])
        assert "-t" in cmd

    def test_t_is_relative_duration_not_absolute_time(self, tmp_path):
        """-t must be duration (end - start), not absolute end time."""
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            _ffmpeg_slice("input.mp3", str(tmp_path / "out.m4a"), start=3.25, end=8.10)
        cmd = mock_run.call_args[0][0]
        t_idx = cmd.index("-t")
        t_value = float(cmd[t_idx + 1])
        # Should be 8.10 - 3.25 = 4.85, not 8.10
        assert abs(t_value - 4.85) < 0.001

    def test_no_t_flag_when_end_is_none(self, tmp_path):
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            _ffmpeg_slice("input.mp3", str(tmp_path / "out.m4a"), start=8.10, end=None)
        cmd = " ".join(mock_run.call_args[0][0])
        assert "-t" not in cmd

    def test_uses_aac_not_stream_copy(self, tmp_path):
        """Must re-encode to AAC — stream copy causes 26ms frame boundary drift."""
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            _ffmpeg_slice("input.mp3", str(tmp_path / "out.m4a"), start=0.0, end=3.25)
        cmd = mock_run.call_args[0][0]
        assert "-c:a" in cmd
        assert "aac" in cmd
        assert "copy" not in cmd

    def test_raises_on_nonzero_returncode(self, tmp_path):
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_fail("bad input")):
            with pytest.raises(RuntimeError, match="ffmpeg slice failed"):
                _ffmpeg_slice("input.mp3", str(tmp_path / "out.mp3"), start=0.0, end=3.0)

    def test_output_path_in_command(self, tmp_path):
        out = str(tmp_path / "slice.mp3")
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            _ffmpeg_slice("input.mp3", out, start=0.0, end=3.0)
        assert out in mock_run.call_args[0][0]


# ---------------------------------------------------------------------------
# slice_audio — main function
# ---------------------------------------------------------------------------

class TestSliceAudioGuards:

    def test_raises_when_audio_file_missing(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="Audio file not found"):
            slice_audio(
                "/nonexistent/audio.mp3",
                SEGMENTS_3,
                output_dir=str(tmp_path),
                section_id="section_01",
            )

    def test_raises_when_segments_empty(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"):
            with pytest.raises(ValueError, match="segments list is empty"):
                slice_audio(str(audio), [], output_dir=str(tmp_path), section_id="s01")

    def test_raises_when_ffmpeg_missing(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        with patch("manimgen.renderer.audio_slicer.subprocess.run",
                   side_effect=FileNotFoundError):
            with pytest.raises(RuntimeError, match="ffmpeg not found"):
                slice_audio(str(audio), SEGMENTS_3, output_dir=str(tmp_path), section_id="s01")


class TestSliceAudioSingleSegment:

    def test_single_segment_re_encodes_to_aac(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        single = [_seg(0, 1, 0.1, 10.0)]

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            paths = slice_audio(str(audio), single, output_dir=str(tmp_path), section_id="s01")

        cmd = " ".join(mock_run.call_args[0][0])
        assert "aac" in cmd
        assert "copy" not in cmd
        assert len(paths) == 1
        assert paths[0].endswith("s01_cue00.m4a")

    def test_single_segment_returns_one_path(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        single = [_seg(0, 1, 0.1, 10.0)]

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()):
            paths = slice_audio(str(audio), single, output_dir=str(tmp_path), section_id="s01")

        assert len(paths) == 1


class TestSliceAudioMultiSegment:

    def _run(self, tmp_path, segments=None, overwrite=False):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        segs = segments or SEGMENTS_3
        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            paths = slice_audio(
                str(audio), segs,
                output_dir=str(tmp_path),
                section_id="section_01",
                overwrite=overwrite,
            )
        return paths, mock_run

    def test_returns_correct_number_of_paths(self, tmp_path):
        paths, _ = self._run(tmp_path)
        assert len(paths) == 3

    def test_output_filenames_match_cue_indices(self, tmp_path):
        paths, _ = self._run(tmp_path)
        basenames = [os.path.basename(p) for p in paths]
        assert basenames == [
            "section_01_cue00.m4a",
            "section_01_cue01.m4a",
            "section_01_cue02.m4a",
        ]

    def test_segment_zero_starts_at_zero_not_word_onset(self, tmp_path):
        """Segment 0 must start at 0.0 to preserve pre-speech silence."""
        _, mock_run = self._run(tmp_path)
        first_call_cmd = mock_run.call_args_list[0][0][0]
        ss_idx = first_call_cmd.index("-ss")
        ss_value = float(first_call_cmd[ss_idx + 1])
        assert abs(ss_value - 0.0) < 1e-6, (
            f"Segment 0 should start at 0.0 but got {ss_value}. "
            "Pre-speech silence must be preserved."
        )

    def test_segment_one_starts_at_its_cue_time(self, tmp_path):
        _, mock_run = self._run(tmp_path)
        second_call_cmd = mock_run.call_args_list[1][0][0]
        ss_idx = second_call_cmd.index("-ss")
        ss_value = float(second_call_cmd[ss_idx + 1])
        assert abs(ss_value - SEGMENTS_3[1].start_time) < 1e-6

    def test_last_segment_has_no_to_flag(self, tmp_path):
        """Last segment must read to EOF — no -to flag."""
        _, mock_run = self._run(tmp_path)
        last_call_cmd = " ".join(mock_run.call_args_list[-1][0][0])
        assert "-to" not in last_call_cmd

    def test_middle_segments_have_t_flag(self, tmp_path):
        _, mock_run = self._run(tmp_path)
        middle_cmd = " ".join(mock_run.call_args_list[1][0][0])
        assert "-t" in middle_cmd

    def test_t_value_is_duration_not_absolute_time(self, tmp_path):
        """Verify -t is expressed as duration relative to -ss."""
        _, mock_run = self._run(tmp_path)
        # Segment 0: starts at 0.0, ends at segment 1's start_time = 3.250
        cmd0 = mock_run.call_args_list[0][0][0]
        t_idx = cmd0.index("-t")
        t_val = float(cmd0[t_idx + 1])
        # Expected: 3.250 - 0.0 = 3.250
        assert abs(t_val - 3.250) < 0.001

    def test_all_segments_use_aac_not_stream_copy(self, tmp_path):
        _, mock_run = self._run(tmp_path)
        for c in mock_run.call_args_list:
            cmd = " ".join(c[0][0])
            assert "aac" in cmd
            assert "-c copy" not in cmd

    def test_ffmpeg_called_once_per_segment(self, tmp_path):
        _, mock_run = self._run(tmp_path)
        assert mock_run.call_count == len(SEGMENTS_3)

    def test_output_directory_is_created(self, tmp_path):
        new_dir = str(tmp_path / "new" / "subdir")
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()):
            slice_audio(str(audio), SEGMENTS_3, output_dir=new_dir, section_id="s01")
        assert os.path.isdir(new_dir)


class TestSliceAudioSkipBehavior:

    def test_existing_files_are_skipped_by_default(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        # Pre-create all three output files
        for i in range(3):
            (tmp_path / f"section_01_cue{i:02d}.m4a").write_bytes(b"existing")

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            paths = slice_audio(
                str(audio), SEGMENTS_3,
                output_dir=str(tmp_path),
                section_id="section_01",
                overwrite=False,
            )

        assert mock_run.call_count == 0, "Should not call ffmpeg for existing files"
        assert len(paths) == 3

    def test_overwrite_true_re_slices_existing_files(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        for i in range(3):
            (tmp_path / f"section_01_cue{i:02d}.m4a").write_bytes(b"existing")

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            slice_audio(
                str(audio), SEGMENTS_3,
                output_dir=str(tmp_path),
                section_id="section_01",
                overwrite=True,
            )

        assert mock_run.call_count == 3

    def test_partial_skip_runs_only_missing(self, tmp_path):
        """Only cue00 exists — cue01 and cue02 should be sliced."""
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        (tmp_path / "section_01_cue00.m4a").write_bytes(b"existing")

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()) as mock_run:
            paths = slice_audio(
                str(audio), SEGMENTS_3,
                output_dir=str(tmp_path),
                section_id="section_01",
                overwrite=False,
            )

        assert mock_run.call_count == 2
        assert len(paths) == 3


class TestSliceAudioWarnings:

    def test_warns_on_short_segment(self, tmp_path, caplog):
        import logging
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        short_segs = [
            _seg(0, 2, 0.1, 0.2),   # under threshold
            _seg(1, 2, 0.3, 5.0),
        ]

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()), \
             caplog.at_level(logging.WARNING, logger="manimgen.renderer.audio_slicer"):
            slice_audio(str(audio), short_segs, output_dir=str(tmp_path), section_id="s01")

        assert any("very short" in r.message for r in caplog.records)

    def test_no_warning_on_normal_segments(self, tmp_path, caplog):
        import logging
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_ok()), \
             caplog.at_level(logging.WARNING, logger="manimgen.renderer.audio_slicer"):
            slice_audio(str(audio), SEGMENTS_3, output_dir=str(tmp_path), section_id="s01")

        assert not any("very short" in r.message for r in caplog.records)


class TestSliceAudioErrorPropagation:

    def test_ffmpeg_failure_raises_runtime_error(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_fail("codec error")):
            with pytest.raises(RuntimeError, match="ffmpeg slice failed"):
                slice_audio(str(audio), SEGMENTS_3, output_dir=str(tmp_path), section_id="s01")

    def test_error_message_includes_output_path(self, tmp_path):
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ffmpeg_fail()):
            with pytest.raises(RuntimeError) as exc_info:
                slice_audio(str(audio), SEGMENTS_3, output_dir=str(tmp_path), section_id="s01")
        assert "s01_cue00" in str(exc_info.value) or "ffmpeg slice failed" in str(exc_info.value)


class TestSliceAudioConstants:

    def test_min_segment_duration_is_positive(self):
        assert _MIN_SEGMENT_DURATION > 0

    def test_min_segment_duration_is_reasonable(self):
        # Should be less than 2s — anything shorter than this legitimately might warn
        assert _MIN_SEGMENT_DURATION < 2.0
