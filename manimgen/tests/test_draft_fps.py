"""The test profile: half size and draft_fps, from the command line to the clips.

Mocked checks first (command lines, config), then real ffmpeg and manimgl runs
that read the result back with ffprobe.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from manimgen import config, paths
from manimgen.renderer import assembler, captions, muxer
from manimgen.renderer.tts import WordTimestamp
from manimgen.validator import render_command


def _probe(path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate",
            "-of", "json", str(path),
        ],
        capture_output=True, text=True, check=True, timeout=30,
    )  # fmt: skip
    s = json.loads(out.stdout)["streams"][0]
    num, den = (int(n) for n in s["r_frame_rate"].split("/"))
    return {"width": s["width"], "height": s["height"], "fps": num / den}


# --- config and paths ------------------------------------------------------


def test_draft_fps_default():
    assert config.DEFAULTS["rendering"]["draft_fps"] == 30
    assert paths.render_fps(draft=True) == 30
    assert paths.render_fps(draft=False) == paths.render_fps()


def test_process_wide_draft_switch_changes_fps_and_size():
    final_fps = paths.render_fps()
    paths.set_draft(True)
    assert paths.render_fps() == 30
    assert paths.render_resolution() == "960x540"
    paths.set_video_format("short")
    assert paths.render_resolution() == "540x960"
    paths.set_draft(False)
    assert paths.render_fps() == final_fps
    assert paths.render_resolution() == "1080x1920"


def test_per_call_draft_does_not_touch_the_global_switch():
    assert paths.render_fps(draft=True) == 30
    assert paths.is_draft() is False


# --- manimgl command line ---------------------------------------------------


def test_final_command_has_no_config_file_and_never_fps():
    cmd = render_command.build_manimgl_command("s.py", "S", draft=False)
    assert "--config_file" not in cmd and "--fps" not in cmd


def test_draft_command_sets_fps_through_a_config_file_never_fps(tmp_path, monkeypatch):
    monkeypatch.setattr(render_command.tempfile, "gettempdir", lambda: str(tmp_path))
    paths.set_video_format("short")
    cmd = render_command.build_manimgl_command("s.py", "S", draft=True)
    assert "--fps" not in cmd
    assert cmd[cmd.index("-r") + 1] == "540x960"
    cfg = Path(cmd[cmd.index("--config_file") + 1])
    assert cfg.read_text(encoding="utf-8") == "camera:\n  fps: 30\n"


def test_landscape_draft_is_half_size_with_30_fps(tmp_path, monkeypatch):
    monkeypatch.setattr(render_command.tempfile, "gettempdir", lambda: str(tmp_path))
    paths.set_draft(True)
    cmd = render_command.build_manimgl_command("s.py", "S")
    assert cmd[cmd.index("-r") + 1] == "960x540"
    assert "--config_file" in cmd


def test_draft_fps_follows_the_configured_value(tmp_path, monkeypatch):
    monkeypatch.setattr(render_command.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setitem(paths._RENDERING, "draft_fps", 24)
    cmd = render_command.build_manimgl_command("s.py", "S", draft=True)
    assert (
        Path(cmd[cmd.index("--config_file") + 1]).read_text() == "camera:\n  fps: 24\n"
    )


# --- cutter, muxer, assembler, captions follow the draft fps ----------------


def test_cue_cut_uses_the_draft_fps(tmp_path):
    paths.set_draft(True)
    seen = []

    class Done:
        returncode = 0
        stderr = ""
        stdout = ""

    def fake(cmd, **kw):
        seen.append(cmd)
        Path(cmd[-1]).write_bytes(b"x")
        return Done()

    with patch.object(muxer.subprocess, "run", side_effect=fake):
        muxer._cut_one("in.mp4", 1.0, 2.0, str(tmp_path / "o.mp4"), 0)
    cmd = seen[0]
    assert cmd[cmd.index("-r") + 1] == "30"
    assert str(muxer.cue_frame_span(1.0, 2.0, 30)[1]) in cmd


def test_cue_frame_grid_is_in_frames_of_the_draft_rate():
    assert muxer.cue_frame_span(0.0, 2.0, 30) == (0, 60)
    assert muxer.cue_frame_span(0.0, 2.0, 60) == (0, 120)


def test_assembler_normalizes_to_draft_fps_and_size():
    paths.set_video_format("short")
    paths.set_draft(True)
    assert assembler._vf_scale() == "scale=540:960,fps=30,format=yuv420p"
    paths.set_draft(False)
    assert (
        assembler._vf_scale()
        == f"scale=1080:1920,fps={paths.render_fps()},format=yuv420p"
    )


def test_muxer_placeholder_follows_draft(tmp_path):
    paths.set_draft(True)
    paths.set_video_format("short")
    cmds = []

    def fake(cmd, **kw):
        cmds.append(cmd)
        Path(cmd[-1]).write_bytes(b"x")

        class R:
            returncode = 0
            stderr = ""
            stdout = ""

        return R()

    with (
        patch.object(muxer, "_get_duration", side_effect=[1.0, 2.0]),
        patch.object(muxer, "_has_video_stream", return_value=False),
        patch.object(muxer.subprocess, "run", side_effect=fake),
    ):
        muxer.mux_audio_video("v.mp4", "a.m4a", str(tmp_path / "o.mp4"))
    src = cmds[0][cmds[0].index("-i") + 1]
    assert "s=540x960" in src and "r=30" in src


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs ffmpeg and ffprobe",
)


@needs_ffmpeg
def test_real_cut_mux_assemble_and_captions_stay_at_30_fps(tmp_path, monkeypatch):
    """A 540x960 30 fps render goes through every step and comes out 30 fps."""
    paths.set_draft(True)
    paths.set_video_format("short")
    monkeypatch.setitem(paths._PATHS, "videos", str(tmp_path / "videos"))
    src = tmp_path / "section.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=540x960:rate=30:duration=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
        check=True, capture_output=True, timeout=120,
    )  # fmt: skip
    durations = [2.0, 2.0]
    clips = muxer.cut_video_at_cues(
        str(src), [0.0, 2.0], durations, str(tmp_path / "muxed"), "section_01"
    )
    muxed = []
    for i, (clip, dur) in enumerate(zip(clips, durations)):
        audio = tmp_path / f"a{i}.m4a"
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=%s" % dur,
             "-c:a", "aac", "-ar", "48000", str(audio)],
            check=True, capture_output=True, timeout=60,
        )  # fmt: skip
        out = tmp_path / "muxed" / f"section_01_cue{i:02d}.mp4"
        muxer.mux_audio_video(clip, str(audio), str(out))
        assert _probe(out)["fps"] == pytest.approx(30, abs=0.01)
        muxed.append(str(out))
    final = assembler.assemble_video(muxed, "fps check")
    info = _probe(final)
    assert (info["width"], info["height"]) == (540, 960)
    # The concat timebase makes the nominal rate of a joined file a hair off.
    assert info["fps"] == pytest.approx(30, abs=0.15)

    words = [WordTimestamp("hello", 0.2, 0.8), WordTimestamp("there", 0.9, 1.5)]
    captions.burn_captions(final, captions.chunk_words(words))
    assert _probe(final)["fps"] == pytest.approx(30, abs=0.15)


# --- a real manimgl render at the test profile ------------------------------


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return sys.platform != "win32"


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("manimlib") is None
    or shutil.which("manimgl") is None
    or shutil.which("ffprobe") is None
    or not _has_display(),
    reason="needs manimgl, ffprobe and a display (xvfb-run on Linux)",
)
@pytest.mark.parametrize(
    "fmt, size", [("short", (540, 960)), ("landscape", (960, 540))]
)
def test_real_draft_render_is_half_size_at_30_fps(tmp_path, monkeypatch, fmt, size):
    from tests.kit_scenes import SCENES

    scene = tmp_path / "EquationScene.py"
    code = SCENES["EquationScene"]
    if fmt == "landscape":
        code = code.replace("(ShortScene)", "(Scene)").replace(
            "from manimgen.scenekit import *", "from manimlib import *"
        )
        code = "from manimlib import *\n\n\nclass EquationScene(Scene):\n    def construct(self):\n        t = Text('x')\n        self.play(FadeIn(t))\n        self.wait(1)\n"
    scene.write_text(code, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    paths.set_video_format(fmt)
    paths.set_draft(True)
    res = render_command.run_manimgl(str(scene), "EquationScene", timeout=600)
    assert res.ok, res.stderr[-500:]
    info = _probe((tmp_path / res.video_path).resolve())
    assert (info["width"], info["height"]) == size
    assert info["fps"] == pytest.approx(30, abs=0.01)
