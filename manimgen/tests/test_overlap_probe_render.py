"""Opt-in: the overlap probe inside a REAL manimgl render (#97).

Skipped unless manimlib is importable and a display is available (on Linux:
run the suite under ``xvfb-run -a``). The tiny scene renders in a few seconds.
The two real offending scenes from the first end-to-end run take about 40 s
each, so they also need ``MANIMGEN_OVERLAP_FIXTURES=1``.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from manimgen import paths
from manimgen.validator import render_command

FIXTURES = Path(__file__).parent / "fixtures" / "overlap"


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def _has_opengl() -> bool:
    # A CI runner can have manimgl and no usable OpenGL (GitHub's Windows
    # runner fails with "wglCreateContextAttribsARB not found"). Ask a child
    # process, so a driver crash cannot take the test session down.
    code = "import moderngl; moderngl.create_standalone_context().release()"
    try:
        return (
            subprocess.run(
                [sys.executable, "-c", code], capture_output=True, timeout=60
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


_CAN_RENDER = (
    importlib.util.find_spec("manimlib") is not None
    and shutil.which("manimgl") is not None
    and _has_display()
    and _has_opengl()
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not _CAN_RENDER,
        reason="needs manimgl, a display (xvfb-run on Linux) and OpenGL 3.3",
    ),
]

_TINY = """from manimlib import *


class TwoTexts(Scene):
    def construct(self):
        a = Text("first caption", font_size=48)
        b = Text("second caption", font_size=48).shift(RIGHT * 0.3)
        c = Text("well apart", font_size=36).to_edge(DOWN)
        box = Rectangle(width=3, height=1)
        inside = Text("in a box", font_size=30).move_to(box)
        group = VGroup(box, inside).to_edge(UP)
        self.play(FadeIn(a), FadeIn(c), FadeIn(group), run_time=0.3)
        self.wait(0.2)
        self.play(FadeIn(b), run_time=0.3)
        self.wait(0.2)
"""


def _render(tmp_path, monkeypatch, src: Path, cls: str):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(paths, "render_quality_flag", lambda: "-l")
    monkeypatch.delenv("MANIMGEN_OVERLAP_PROBE", raising=False)
    scene = tmp_path / src.name
    scene.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    return render_command.run_manimgl(str(scene), cls, timeout=600)


@pytest.mark.timeout(300)
def test_two_overlapping_texts_are_reported(tmp_path, monkeypatch):
    src = tmp_path / "src" / "tiny.py"
    src.parent.mkdir()
    src.write_text(_TINY, encoding="utf-8")
    res = _render(tmp_path, monkeypatch, src, "TwoTexts")
    assert res.ok, res.stderr
    assert res.probe_error is None
    pairs = [{o.a, o.b} for o in res.overlaps]
    assert pairs == [{"first caption", "second caption"}]
    assert res.overlaps[0].time == pytest.approx(0.8, abs=0.05)


_FIXTURES_ON = os.environ.get("MANIMGEN_OVERLAP_FIXTURES") == "1"


@pytest.mark.timeout(900)
@pytest.mark.skipif(not _FIXTURES_ON, reason="set MANIMGEN_OVERLAP_FIXTURES=1")
@pytest.mark.parametrize(
    "name, cls, expected",
    [
        ("first_run_section_05.py", "Section05Scene", "of professional programmers"),
        ("first_run_section_02.py", "Section02Scene", "One comparison."),
    ],
)
def test_first_run_offending_scenes_are_reported(
    tmp_path, monkeypatch, name, cls, expected
):
    res = _render(tmp_path, monkeypatch, FIXTURES / name, cls)
    assert res.ok, res.stderr
    assert any(expected in o.a or expected in o.b for o in res.overlaps)
