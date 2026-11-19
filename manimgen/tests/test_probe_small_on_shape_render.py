"""Probe findings `small` and `on_shape` on hand-written, non-kit vertical scenes.

The scenes are the calibration set: two that must come out clean (labels beside
shapes, text centred in a faint fill, tick numbers, a dark backing plate) and
two that must be reported (text over a line, on a solid fill and on a corner;
text below font_size 22). Real renders at 1080x1920 (xvfb on Linux).
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys

import pytest

from manimgen import paths
from manimgen.validator import render_command


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

GOOD1 = """from manimlib import *


class Good1(Scene):
    def construct(self):
        sq = (
            Square(side_length=1.2)
            .set_stroke(BLUE, 4)
            .set_fill(BLUE, 0.25)
            .move_to([-0.3, 1.5, 0])
        )
        inside = Text("16", font_size=28).move_to(sq)
        ln = Line([-1.5, 0.3, 0], [0.9, 0.3, 0], stroke_width=4)
        lab = Text("length 4", font_size=22).next_to(ln, DOWN, buff=0.15)
        arr = Arrow([-1.5, -0.6, 0], [0.9, -0.6, 0])
        la = Text("push", font_size=22).next_to(arr, UP, buff=0.1)
        br = Brace(Line([-1.5, -1.2, 0], [0.9, -1.2, 0]), DOWN, buff=0.1)
        lb = Text("12 cm", font_size=22).next_to(br, DOWN, buff=0.12)
        self.add(sq, inside, ln, lab, arr, la, br, lb)
        self.wait(0.2)
"""

GOOD2 = """from manimlib import *


class Good2(Scene):
    def construct(self):
        t = Text("boxed word", font_size=28).move_to([-0.3, 1.8, 0])
        r = SurroundingRectangle(t, buff=0.12, color=YELLOW)
        u = Underline(Text("under me", font_size=28).move_to([-0.3, 0.8, 0]), buff=0.1)
        t2 = Text("under me", font_size=28).move_to([-0.3, 0.8, 0])
        c = (
            Circle(radius=0.6)
            .set_stroke(RED, 4)
            .set_fill(RED, 0.2)
            .move_to([-0.3, -0.5, 0])
        )
        ct = Tex("x^2", font_size=36).move_to(c)
        plate = (
            Rectangle(width=2, height=0.5)
            .set_fill("#1C1C1C", 1)
            .set_stroke(WHITE, 2)
            .move_to([-0.3, -1.4, 0])
        )
        pt = Text("on plate", font_size=22).move_to(plate)
        self.add(t, r, u, t2, c, ct, plate, pt)
        self.wait(0.2)
"""

BAD_ON_SHAPE = """from manimlib import *


class Bad1(Scene):
    def construct(self):
        ln = Line([-1.8, 1.5, 0], [1.2, 1.5, 0], stroke_width=4)
        t1 = Text("through a line", font_size=28).move_to([-0.3, 1.5, 0])
        sq = (
            Square(side_length=1.5)
            .set_stroke(GREEN, 4)
            .set_fill(GREEN, 1.0)
            .move_to([-0.3, 0, 0])
        )
        t2 = Text("on solid fill", font_size=28).move_to(sq)
        sq2 = Square(side_length=1.0).set_stroke(RED, 4).move_to([-0.3, -1.3, 0])
        t3 = Text("edge", font_size=28).move_to(sq2.get_corner(UR))
        self.add(ln, t1, sq, t2, sq2, t3)
        self.wait(0.2)
"""

BAD_SMALL = """from manimlib import *


class Bad2(Scene):
    def construct(self):
        a = Text("tiny scaled", font_size=28).scale(0.5).move_to([-0.3, 1.5, 0])
        b = Text("font 14", font_size=14).move_to([-0.3, 0.8, 0])
        c = Tex("a^2+b^2", font_size=14).move_to([-0.3, 0, 0])
        ok = Text("fine 22", font_size=22).move_to([-0.3, -0.8, 0])
        grp = (
            VGroup(Square(side_length=3), Text("in group", font_size=28))
            .scale(0.4)
            .move_to([-0.3, -1.2, 0])
        )
        self.add(a, b, c, ok, grp)
        self.wait(0.2)
"""


def _render(tmp_path, monkeypatch, source, cls):
    monkeypatch.chdir(tmp_path)
    scene = tmp_path / f"{cls}.py"
    scene.write_text(source, encoding="utf-8")
    paths.set_video_format("short")
    try:
        return render_command.run_manimgl(str(scene), cls, timeout=600)
    finally:
        paths.set_video_format("landscape")


@pytest.mark.timeout(600)
@pytest.mark.parametrize("source, cls", [(GOOD1, "Good1"), (GOOD2, "Good2")])
def test_good_scenes_have_no_findings(tmp_path, monkeypatch, source, cls):
    res = _render(tmp_path, monkeypatch, source, cls)
    assert res.ok, res.stderr
    assert res.probe_error is None
    assert not res.overlaps, [(o.kind, o.a, o.b) for o in res.overlaps]


@pytest.mark.timeout(600)
def test_text_on_a_line_a_fill_and_an_edge_is_reported(tmp_path, monkeypatch):
    res = _render(tmp_path, monkeypatch, BAD_ON_SHAPE, "Bad1")
    assert res.ok, res.stderr
    found = {o.a for o in res.overlaps if o.kind == "on_shape"}
    assert found == {"through a line", "on solid fill", "edge"}


@pytest.mark.timeout(600)
def test_text_below_font_size_22_is_reported(tmp_path, monkeypatch):
    res = _render(tmp_path, monkeypatch, BAD_SMALL, "Bad2")
    assert res.ok, res.stderr
    found = {o.a for o in res.overlaps if o.kind == "small"}
    assert found == {"tiny scaled", "font 14", "a^2+b^2", "in group"}
    assert "fine 22" not in {o.a for o in res.overlaps}
