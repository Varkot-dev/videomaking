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
import sys
from pathlib import Path

import pytest

from manimgen import paths
from manimgen.validator import render_command

FIXTURES = Path(__file__).parent / "fixtures" / "overlap"


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    # GitHub's Windows runner has no OpenGL driver (manimgl fails with
    # "wglCreateContextAttribsARB not found"). A real render on Windows is
    # covered by the nightly render workflow and the library PC checklist.
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


_OUT_OF_BOUNDS = """from manimlib import *

class Misplaced(Scene):
    def construct(self):
        wide = Text("This sentence is far too wide for the frame", font_size=36)
        wide.move_to([0, 2.3, 0])
        low = Text("under the caption", font_size=28).move_to([-0.3, -2.6, 0])
        good = Text("fine here", font_size=28).move_to([-0.3, 0.9, 0])
        self.add(wide, low, good)
        self.wait(0.3)
"""


@pytest.mark.timeout(300)
def test_vertical_short_reports_offscreen_and_unsafe_text(tmp_path, monkeypatch):
    """A real 1080x1920 render: text past the frame edge and text under the
    Shorts caption are reported; text inside the safe area is not."""
    paths.set_video_format("short")
    src = tmp_path / "src" / "misplaced.py"
    src.parent.mkdir()
    src.write_text(_OUT_OF_BOUNDS, encoding="utf-8")
    res = _render(tmp_path, monkeypatch, src, "Misplaced")
    assert res.ok, res.stderr
    assert res.probe_error is None
    found = {(o.a, o.kind) for o in res.overlaps}
    assert ("under the caption", "unsafe") in found
    assert any(kind == "offscreen" for _, kind in found)
    assert not any(text == "fine here" for text, _ in found)


_SLIGHTLY_OUT = """from manimlib import *

class SlightlyOut(Scene):
    def construct(self):
        hook = VGroup(
            Text("Why does 3-4-5", font_size=28),
            Text("make a right angle?", font_size=28),
        ).arrange(DOWN, buff=0.12).move_to([-0.3, 2.6, 0])
        self.add(hook)
        grid = Square(side_length=2).move_to([-0.3, -0.7, 0])
        label = Text("4", font_size=28).next_to(grid, DOWN, buff=0.15)
        self.play(ShowCreation(grid), FadeIn(label), run_time=0.3)
        self.wait(0.2)
"""


@pytest.mark.timeout(300)
def test_vertical_short_nudges_text_that_is_slightly_out(tmp_path, monkeypatch):
    """The two mistakes the 12-run experiment kept making (a hook a little too
    high, a label just under the diagram) are moved inside the safe area before
    they are drawn, so the probe has nothing to report."""
    import json

    paths.set_video_format("short")
    src = tmp_path / "src" / "slightly_out.py"
    src.parent.mkdir()
    src.write_text(_SLIGHTLY_OUT, encoding="utf-8")
    res = _render(tmp_path, monkeypatch, src, "SlightlyOut")
    assert res.ok, res.stderr
    assert res.probe_error is None
    assert not res.overlaps, res.overlaps
    sidecar = Path(res.video_path + ".overlaps.json")
    assert json.loads(sidecar.read_text(encoding="utf-8"))["nudges"] == 2
