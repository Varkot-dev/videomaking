"""Tests for frame sampling shared by frame_checker and layout_checker (#84).

Covers the cue-end timestamp helper, the evenly spaced cap on scene cuts, the
-fps_mode flag with its logged -vsync fallback, logged fallbacks on error, and
the downscale of frames sent to the vision model. Tests marked with the
ffmpeg fixture run the real binary on tiny synthetic videos and are skipped
when ffmpeg is not installed.
"""

from __future__ import annotations

import base64
import io
import logging
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from manimgen.validator import frame_checker as fc
from manimgen.validator import layout_checker as lc

_FFMPEG_TIMEOUT_SECONDS = 60


@pytest.fixture
def ffmpeg():
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    return "ffmpeg"


def _make_video(path, size: str, colors: list[str], seconds_each: float = 1.0):
    """Write a small mp4 made of solid-colour segments (one scene cut per change)."""
    inputs: list[str] = []
    for color in colors:
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s={size}:d={seconds_each}"]
    n = len(colors)
    concat = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[v]"
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *inputs]
        + ["-filter_complex", concat, "-map", "[v]", "-pix_fmt", "yuv420p", str(path)],
        check=True,
        capture_output=True,
        timeout=_FFMPEG_TIMEOUT_SECONDS,
    )
    return str(path)


# -- evenly_capped ------------------------------------------------------------


def test_evenly_capped_spreads_across_the_whole_range():
    values = [float(i) for i in range(20)]
    picked = fc.evenly_capped(values, 8)
    assert len(picked) == 8
    assert picked[0] == 0.0 and picked[-1] == 19.0
    assert picked == sorted(picked)


def test_evenly_capped_keeps_short_lists_sorted():
    assert fc.evenly_capped([3.0, 1.0, 2.0], 8) == [1.0, 2.0, 3.0]
    assert fc.evenly_capped([1.0, 2.0], 0) == []


# -- cue_end_timestamps / sample_timestamps ------------------------------------


def test_cue_end_timestamps_sample_just_before_each_cue_ends():
    assert fc.cue_end_timestamps(9.0, [2.0, 3.0, 4.0]) == [1.9, 4.9, 8.9]


def test_cue_end_timestamps_clamped_to_video_and_skip_bad_cues():
    stamps = fc.cue_end_timestamps(5.0, [2.0, 0.0, 10.0, 3.0])
    assert stamps == [1.9, 4.95]


def test_cue_end_timestamps_short_cue_stays_inside_its_cue():
    # A 0.1s cue samples at its midpoint, never at or before its own start.
    assert fc.cue_end_timestamps(5.0, [1.0, 0.1]) == [0.9, 1.05]


def test_cue_end_timestamps_capped_evenly():
    stamps = fc.cue_end_timestamps(30.0, [1.0] * 30, cap=5)
    assert len(stamps) == 5
    assert stamps[0] == pytest.approx(0.9)
    assert stamps[-1] == pytest.approx(29.9)


@pytest.mark.parametrize("cues", [None, [], [0.0], ["x"]])
def test_cue_end_timestamps_empty_without_usable_cues(cues):
    assert fc.cue_end_timestamps(10.0, cues) == []


def test_sample_timestamps_falls_back_to_default_without_cues():
    assert fc.sample_timestamps(8.0, None, default=[2.0, 4.0, 6.0]) == [2.0, 4.0, 6.0]
    assert fc.sample_timestamps(8.0, [4.0, 4.0], default=[2.0]) == [3.9, 7.9]


# -- _scene_guided_timestamps ---------------------------------------------------


def _showinfo(times):
    return "\n".join(f"[Parsed_showinfo_1] n:0 pts:0 pts_time:{t}" for t in times)


def test_scene_guided_uses_fps_mode_and_samples_whole_video():
    cuts = [float(t) for t in range(2, 62, 2)]  # 30 cuts over a 62s video
    with patch.object(
        fc.subprocess,
        "run",
        return_value=MagicMock(returncode=0, stderr=_showinfo(cuts)),
    ) as run:
        stamps = fc._scene_guided_timestamps("/v.mp4", 62.0)

    cmd = run.call_args.args[0]
    assert "-fps_mode" in cmd and "-vsync" not in cmd
    assert run.call_args.kwargs["timeout"] > 0
    assert len(stamps) == 8
    assert stamps[0] == 2.0 and stamps[-1] == 60.0, (
        "must not keep only the earliest cuts"
    )


def test_scene_guided_falls_back_to_vsync_when_fps_mode_is_rejected(caplog):
    rejected = MagicMock(
        returncode=1, stderr="Unrecognized option 'fps_mode'.\nError splitting"
    )
    ok = MagicMock(returncode=0, stderr=_showinfo([1.0, 2.0]))
    with patch.object(fc.subprocess, "run", side_effect=[rejected, ok]) as run:
        with caplog.at_level(logging.INFO, logger=fc.logger.name):
            stamps = fc._scene_guided_timestamps("/v.mp4", 3.0)

    assert "-fps_mode" in run.call_args_list[0].args[0]
    assert "-vsync" in run.call_args_list[1].args[0]
    assert stamps == [1.0, 2.0]
    assert any("fps_mode" in r.getMessage() for r in caplog.records)


def test_scene_guided_logs_fallback_on_exception(caplog):
    with patch.object(fc.subprocess, "run", side_effect=OSError("no ffmpeg")):
        with caplog.at_level(logging.WARNING, logger=fc.logger.name):
            stamps = fc._scene_guided_timestamps("/v.mp4", 4.0)
    assert stamps == [1.0, 2.0, 3.0]
    assert any("no ffmpeg" in r.getMessage() for r in caplog.records)


def test_scene_guided_logs_fallback_on_ffmpeg_failure(caplog):
    failed = MagicMock(returncode=1, stderr="moov atom not found")
    with patch.object(fc.subprocess, "run", return_value=failed) as run:
        with caplog.at_level(logging.WARNING, logger=fc.logger.name):
            stamps = fc._scene_guided_timestamps("/v.mp4", 4.0)
    assert run.call_count == 1, "only an fps_mode rejection retries with -vsync"
    assert stamps == [1.0, 2.0, 3.0]
    assert any("moov atom" in r.getMessage() for r in caplog.records)


def test_scene_guided_real_ffmpeg_finds_cuts_without_fallback(ffmpeg, tmp_path, caplog):
    video = _make_video(
        tmp_path / "cuts.mp4", "160x90", ["red", "blue", "green", "white"]
    )
    with caplog.at_level(logging.INFO, logger=fc.logger.name):
        stamps = fc._scene_guided_timestamps(video, 4.0)
    assert len(stamps) == 3
    assert stamps == sorted(stamps)
    assert all(abs(s - round(s)) < 0.1 for s in stamps), stamps
    assert not caplog.records, [r.getMessage() for r in caplog.records]


# -- check_frames with cue durations ----------------------------------------------


def test_check_frames_samples_cue_ends_when_cues_known(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with (
        patch.object(fc, "probe_video_duration", return_value=6.0),
        patch.object(fc, "_scene_guided_timestamps") as guided,
        patch.object(fc, "_extract_frame_pil", return_value=None) as extract,
    ):
        fc.check_frames(str(video), [2.0, 4.0])
    guided.assert_not_called()
    assert [c.args[1] for c in extract.call_args_list] == [1.9, 5.9]


def test_check_frames_uses_scene_cuts_without_cues(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with (
        patch.object(fc, "probe_video_duration", return_value=6.0),
        patch.object(fc, "_scene_guided_timestamps", return_value=[1.0]) as guided,
        patch.object(fc, "_extract_frame_pil", return_value=None),
    ):
        fc.check_frames(str(video))
    guided.assert_called_once()


# -- layout_checker sampling and downscale -----------------------------------------


def test_layout_sample_frames_uses_cue_ends_capped():
    with (
        patch.object(lc, "probe_video_duration", return_value=20.0),
        patch.object(lc, "_extract_frame", return_value="b64") as extract,
    ):
        frames = lc._sample_frames("/v.mp4", [2.0] * 10)
    stamps = [c.args[1] for c in extract.call_args_list]
    assert len(frames) == len(stamps) == lc._MAX_VISION_FRAMES
    assert stamps[0] == pytest.approx(1.9) and stamps[-1] == pytest.approx(19.9)


def test_layout_sample_frames_keeps_quartiles_without_cues():
    with (
        patch.object(lc, "probe_video_duration", return_value=8.0),
        patch.object(lc, "_extract_frame", return_value="b64") as extract,
    ):
        lc._sample_frames("/v.mp4", None)
    assert [c.args[1] for c in extract.call_args_list] == [2.0, 4.0, 6.0]


def test_check_layout_passes_cues_to_sampler(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    with patch.object(lc, "_sample_frames", return_value=[]) as sample:
        lc.check_layout(str(video), [1.0, 2.0])
    sample.assert_called_once_with(str(video), [1.0, 2.0])


def test_extract_frame_command_downscales():
    with patch.object(
        lc.subprocess, "run", return_value=MagicMock(returncode=1)
    ) as run:
        lc._extract_frame("/v.mp4", 1.0)
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("-vf") + 1] == lc._DOWNSCALE_FILTER
    assert run.call_args.kwargs["timeout"] > 0


def _png_size(b64: str) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(b64))) as img:
        return img.size


def test_extract_frame_real_ffmpeg_downscales_1080p_to_960(ffmpeg, tmp_path):
    pytest.importorskip("PIL")
    video = _make_video(tmp_path / "hd.mp4", "1920x1080", ["0x1C1C1C"])
    b64 = lc._extract_frame(video, 0.5)
    assert b64 is not None
    assert _png_size(b64) == (960, 540)


def test_extract_frame_real_ffmpeg_does_not_upscale_small_video(ffmpeg, tmp_path):
    pytest.importorskip("PIL")
    video = _make_video(tmp_path / "sd.mp4", "640x360", ["0x1C1C1C"])
    b64 = lc._extract_frame(video, 0.5)
    assert b64 is not None
    assert _png_size(b64) == (640, 360)


# -- callers pass cue durations through ---------------------------------------------


def test_validate_render_passes_cue_durations_to_both_checkers(tmp_path, monkeypatch):
    from manimgen.validator import render_validator as rv
    from manimgen.validator.frame_checker import FrameCheckResult

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    monkeypatch.setenv(lc.FIRST_PASS_ENV_VAR, "1")
    with (
        patch.object(rv, "check_frames", return_value=FrameCheckResult()) as frames,
        patch.object(
            rv,
            "check_layout",
            return_value={"ok": True, "issues": "", "skipped": False},
        ) as layout,
    ):
        rv.validate_render(str(video), "", "/s.py", [1.5, 2.5], overlaps=[])
    assert frames.call_args.args[1] == [1.5, 2.5]
    assert layout.call_args.args[1] == [1.5, 2.5]
