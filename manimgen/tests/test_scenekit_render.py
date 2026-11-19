"""Real renders of hand-written ShortScene scenes (xvfb on Linux).

The scenes in ``kit_scenes.py`` reproduce layout failures measured in real
Shorts. Each is rendered at 1080x1920 through ``run_manimgl`` (so the safety
gate and the probe run too) and must come out clean: no probe finding of any
kind, no text under 22, nothing outside the safe area, nothing left half-faded.

Set ``MANIMGEN_KIT_FRAME_SHEET=/path/sheet.png`` to also save a contact sheet of
the last frames, for looking at them.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from manimgen import paths
from manimgen.validator import render_command
from manimgen.validator.scene_ast_gate import inspect_scene_code
from tests.kit_scenes import SCENES


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return sys.platform != "win32"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        importlib.util.find_spec("manimlib") is None
        or shutil.which("manimgl") is None
        or not _has_display(),
        reason="needs manimgl and a display (xvfb-run on Linux)",
    ),
]

_RENDERED: dict[str, tuple] = {}  # class name -> (RenderResult, sidecar dict)


def _render(tmp_path_factory, name: str):
    if name in _RENDERED:
        return _RENDERED[name]
    folder = tmp_path_factory.mktemp(name)
    scene = folder / f"{name}.py"
    scene.write_text(SCENES[name], encoding="utf-8")
    cwd = os.getcwd()
    os.chdir(folder)
    try:
        paths.set_video_format("short")
        res = render_command.run_manimgl(str(scene), name, timeout=600)
    finally:
        os.chdir(cwd)
        paths.set_video_format("landscape")
    side = {}
    if res.video_path:
        video = (folder / res.video_path).resolve()
        sidecar = Path(str(video) + ".overlaps.json")
        side = json.loads(sidecar.read_text(encoding="utf-8"))
        side["video"] = str(video)
    _RENDERED[name] = (res, side)
    return _RENDERED[name]


@pytest.fixture(params=sorted(SCENES))
def rendered(request, tmp_path_factory):
    res, side = _render(tmp_path_factory, request.param)
    return request.param, res, side


@pytest.mark.parametrize("name", sorted(SCENES))
def test_scene_source_passes_the_safety_gate(name):
    assert inspect_scene_code(SCENES[name]).ok


@pytest.mark.timeout(600)
def test_kit_scene_renders_clean(rendered):
    name, res, side = rendered
    assert res.ok, res.stderr
    assert res.probe_error is None
    # No finding of any kind: overlap, offscreen, unsafe, small, on_shape.
    assert not res.overlaps, [(o.kind, o.a, o.b) for o in res.overlaps]
    # Every text, label included, is at least what font_size 22 renders at.
    assert side["min_size_ratio"] >= 0.97, side["min_size_ratio"]


@pytest.mark.timeout(600)
def test_kit_audit_after_finish_is_clean(rendered):
    """Nothing outside the safe area and nothing left half-faded at the end."""
    name, res, _ = rendered
    assert res.ok, res.stderr
    line = next(ln for ln in res.stdout.splitlines() if ln.startswith("KIT-AUDIT"))
    audit, findings = ast.literal_eval(line[len("KIT-AUDIT") :].strip())
    assert audit == [], audit
    # The equation scene dims a note on purpose and the kit fades it out.
    expected = {"EquationScene": ["1 mobject(s) were left half-faded; faded out"]}
    assert findings == expected.get(name, []), findings


@pytest.mark.timeout(900)
def test_frame_sheet(tmp_path_factory):
    out = os.environ.get("MANIMGEN_KIT_FRAME_SHEET")
    if not out:
        pytest.skip("set MANIMGEN_KIT_FRAME_SHEET to save a contact sheet")
    from PIL import Image

    frames = []
    for name in sorted(SCENES):
        res, _ = _render(tmp_path_factory, name)
        assert res.ok, res.stderr
        video = _render(tmp_path_factory, name)[1]["video"]
        png = Path(video).with_suffix(".last.png")
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-loglevel",
                "error",
                "-sseof",
                "-0.1",
                "-i",
                video,
                "-frames:v",
                "1",
                str(png),
            ],
            check=True,
            timeout=60,
        )
        frames.append(Image.open(png).convert("RGB"))
    w, h = frames[0].size
    sheet = Image.new("RGB", (w * len(frames) + 20 * (len(frames) - 1), h), "#444")
    for i, frame in enumerate(frames):
        sheet.paste(frame, (i * (w + 20), 0))
    sheet.save(out)
