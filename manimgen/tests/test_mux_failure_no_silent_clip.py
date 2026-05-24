"""
Regression tests for issue #28 — mux failure / missing audio slice must NEVER
silently ship a narration-less (silent) clip into the final video.

Before the fix, cli.py's per-cue mux loop did:
  - except Exception: produced.append(cue_clip)   # silent video, no narration
  - else (audio slice missing): produced.append(cue_clip)  # silent, no log

The assembler then incorporated those silent clips into the FINAL video,
indistinguishable from success. These tests assert that:
  (a) a silent cue_clip is NEVER appended to `produced`, and
  (b) the failure is surfaced (3-state FAILED result + error logged).

Zero LLM calls, zero subprocess calls — mux_audio_video is mocked.
"""

import logging
from unittest.mock import MagicMock

import pytest

import manimgen.cli as cli
from manimgen.cli import _mux_one_cue
from manimgen.types import CueMuxResult, MuxStatus

SECTION = {"id": "section_01", "title": "Intro"}


def _log() -> logging.LoggerAdapter:
    return logging.LoggerAdapter(logging.getLogger("test"), {"section": "section_01"})


@pytest.fixture(autouse=True)
def _isolate_muxed_dir(tmp_path, monkeypatch):
    """Point the muxed output dir at tmp_path.

    _mux_one_cue computes the muxed output path via paths.muxed_dir() and
    short-circuits to SUCCESS if that file already exists. Without this
    isolation the tests collide with real artifacts left in the actual
    manimgen/output/muxed/ dir from prior pipeline runs (e.g. a stale
    section_01_cue00.mp4), making the mock mux never run.
    """
    monkeypatch.setattr(cli.paths, "muxed_dir", lambda: str(tmp_path))


class TestMuxOneCueRetry:
    """_mux_one_cue retries once, then fails — never returns the silent clip."""

    def test_success_first_attempt(self, tmp_path):
        clip = tmp_path / "section_01_cue00_video.mp4"
        clip.write_bytes(b"v")
        audio = tmp_path / "section_01_cue00.m4a"
        audio.write_bytes(b"a")
        mux = MagicMock(return_value="out.mp4")

        result = _mux_one_cue(SECTION, 1, 0, str(clip), str(audio), mux, _log())

        assert result.status is MuxStatus.SUCCESS
        assert result.ok
        assert result.path is not None
        assert result.path != str(clip)  # not the silent clip
        assert mux.call_count == 1

    def test_retry_succeeds_on_second_attempt(self, tmp_path):
        clip = tmp_path / "section_01_cue00_video.mp4"
        clip.write_bytes(b"v")
        audio = tmp_path / "section_01_cue00.m4a"
        audio.write_bytes(b"a")
        mux = MagicMock(side_effect=[RuntimeError("ffmpeg boom"), "out.mp4"])

        result = _mux_one_cue(SECTION, 1, 0, str(clip), str(audio), mux, _log())

        assert result.status is MuxStatus.RETRIED_OK
        assert result.ok
        assert mux.call_count == 2

    def test_mux_raises_both_attempts_marks_failed_no_silent_clip(
        self, tmp_path, caplog
    ):
        clip = tmp_path / "section_01_cue00_video.mp4"
        clip.write_bytes(b"v")
        audio = tmp_path / "section_01_cue00.m4a"
        audio.write_bytes(b"a")
        mux = MagicMock(side_effect=RuntimeError("ffmpeg always fails"))

        with caplog.at_level(logging.ERROR):
            result = _mux_one_cue(SECTION, 1, 0, str(clip), str(audio), mux, _log())

        # (a) silent clip is NOT carried forward
        assert result.status is MuxStatus.FAILED
        assert not result.ok
        assert result.path is None
        assert result.path != str(clip)
        # retried exactly once (two attempts total)
        assert mux.call_count == 2
        # (b) failure surfaced: error logged loudly with cue + underlying error
        assert "FAILED cue 0" in caplog.text
        assert "ffmpeg always fails" in caplog.text
        assert result.error == "ffmpeg always fails"

    def test_missing_audio_slice_marks_failed_no_silent_clip(self, tmp_path, caplog):
        clip = tmp_path / "section_01_cue00_video.mp4"
        clip.write_bytes(b"v")
        missing_audio = str(tmp_path / "does_not_exist.m4a")
        mux = MagicMock()

        with caplog.at_level(logging.ERROR):
            result = _mux_one_cue(SECTION, 1, 0, str(clip), missing_audio, mux, _log())

        assert result.status is MuxStatus.FAILED
        assert not result.ok
        assert result.path is None
        # never even attempts to mux without an audio slice
        assert mux.call_count == 0
        # the silent-clip path was the old bug — assert it's loudly logged now
        assert "FAILED cue 0" in caplog.text
        assert "audio slice missing" in caplog.text


class TestRunSectionDropsFailedSection:
    """_run_section must drop the whole section (return []) when any cue fails,
    so the assembler can never mistake a failed cue for a successful one."""

    def _section_audio(self, tmp_path):
        """Build precomputed section_audio with two real audio slices."""
        from manimgen.types import CueSegment

        a0 = tmp_path / "section_01_cue00.m4a"
        a1 = tmp_path / "section_01_cue01.m4a"
        a0.write_bytes(b"a0")
        a1.write_bytes(b"a1")
        segs = [
            CueSegment(cue_index=0, total_cues=2, start_time=0.0, duration=2.0),
            CueSegment(cue_index=1, total_cues=2, start_time=2.0, duration=3.0),
        ]
        return {
            "segments": segs,
            "audio_slices": [str(a0), str(a1)],
            "cue_durations": [2.0, 3.0],
        }

    def test_failed_mux_yields_no_clips(self, tmp_path, monkeypatch, caplog):
        import manimgen.cli as cli

        section = {"id": "section_01", "title": "Intro", "narration": "hello world"}
        section_audio = self._section_audio(tmp_path)

        # Pretend a fresh render already exists so we skip codegen/render entirely.
        rendered = tmp_path / "Section01Scene.mp4"
        rendered.write_bytes(b"video")
        monkeypatch.setattr(cli, "_find_rendered_video", lambda _c: str(rendered))
        monkeypatch.setattr(cli, "_render_is_fresh", lambda _v, _h: True)

        # Cutter returns two per-cue video clips (silent).
        clip0 = tmp_path / "section_01_cue00_video.mp4"
        clip1 = tmp_path / "section_01_cue01_video.mp4"
        clip0.write_bytes(b"v0")
        clip1.write_bytes(b"v1")
        import manimgen.renderer.cutter as cutter

        monkeypatch.setattr(
            cutter, "cut_video_at_cues", lambda *a, **k: [str(clip0), str(clip1)]
        )

        # Mux always fails (and would also fail the single retry).
        import manimgen.renderer.muxer as muxer

        monkeypatch.setattr(
            muxer,
            "mux_audio_video",
            MagicMock(side_effect=RuntimeError("mux down")),
        )

        # Keep muxed-output paths inside tmp so nothing pre-exists.
        monkeypatch.setattr(cli.paths, "muxed_dir", lambda: str(tmp_path))

        with caplog.at_level(logging.ERROR):
            produced = cli._run_section(
                section,
                idx=1,
                tts_on=True,
                current_topic_hash="abc12345",
                section_audio=section_audio,
            )

        # Section dropped — NO silent clips reach the assembler.
        assert produced == []
        assert str(clip0) not in produced
        assert str(clip1) not in produced
        # Failure is loud and names the section.
        assert "Section 1" in caplog.text
        assert "FAILED" in caplog.text


def test_cue_mux_result_ok_property():
    assert CueMuxResult(0, MuxStatus.SUCCESS, "x.mp4").ok
    assert CueMuxResult(0, MuxStatus.RETRIED_OK, "x.mp4").ok
    assert not CueMuxResult(0, MuxStatus.FAILED, None, "err").ok
