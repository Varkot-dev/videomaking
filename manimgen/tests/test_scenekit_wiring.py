"""How the Shorts layout kit is wired in: safety gate, render child, codeguard,
timing check and the two new probe finding kinds (pure parts, no render)."""

from __future__ import annotations

import os

import pytest

import manimgen
from manimgen import paths
from manimgen.probes import overlap_probe as probe
from manimgen.probes import overlap_report
from manimgen.validator import render_command
from manimgen.validator.codeguard import _fix_short_scene_base, precheck_and_autofix
from manimgen.validator.scene_ast_gate import inspect_scene_code
from manimgen.validator.timing_verifier import verify_timing

pytestmark = pytest.mark.unit

_PKG = os.path.dirname(manimgen.__file__)
_BODY = "\n\nclass S(ShortScene):\n    def construct(self):\n        self.wait(1)\n"


def _all_manimgen_modules() -> list[str]:
    found = []
    for folder, _dirs, files in os.walk(_PKG):
        for name in files:
            if not name.endswith(".py") or name == "__init__.py":
                continue
            rel = os.path.relpath(os.path.join(folder, name), os.path.dirname(_PKG))
            dotted = rel[:-3].replace(os.sep, ".")
            if "scenekit" not in dotted and "bootstrap" not in dotted:
                found.append(dotted)
    return found


class TestGate:
    @pytest.mark.parametrize(
        "line",
        [
            "from manimgen.scenekit import *",
            "from manimgen.scenekit import ShortScene",
            "from manimgen.scenekit.shorts import ShortScene",
            "from manimgen.scenekit.shorts import *",
        ],
    )
    def test_the_kit_import_is_allowed(self, line):
        code = f"from manimlib import *\n{line}\n{_BODY}"
        assert inspect_scene_code(code).ok

    def test_every_other_manimgen_module_is_still_rejected(self):
        modules = _all_manimgen_modules()
        assert len(modules) > 20  # the walk really found the package
        for name in modules + ["manimgen"]:
            for line in (
                f"from {name} import *",
                f"from {name} import x",
                f"import {name}",
            ):
                code = f"from manimlib import *\n{line}\n{_BODY}"
                assert not inspect_scene_code(code).ok, line

    @pytest.mark.parametrize(
        "line",
        [
            "from manimgen import scenekit",
            "from manimgen.scenekit import shorts",
            "from manimgen.scenekit import os",
            "from manimgen.scenekit import ShortScene as S",
            "from manimgen.scenekit.shorts import math",
            "from manimgen.scenekit.geometry import *",
            "from manimgen.scenekit.shorts.x import *",
            "import manimgen.scenekit",
            "import manimgen.scenekit.shorts",
            "from . import scenekit",
        ],
    )
    def test_the_kit_import_is_not_a_back_door(self, line):
        code = f"from manimlib import *\n{line}\n{_BODY}"
        assert not inspect_scene_code(code).ok, line

    def test_the_kit_module_itself_passes_the_gate(self):
        path = os.path.join(_PKG, "scenekit", "shorts.py")
        with open(path, encoding="utf-8") as f:
            result = inspect_scene_code(f.read())
        assert result.ok, result.findings
        assert result.class_count == 1


class TestRenderEnv:
    def test_the_child_can_import_this_manimgen(self):
        env = render_command.with_package_path({"PATH": "/bin"})
        root = os.path.dirname(_PKG)
        assert env["PYTHONPATH"].split(os.pathsep) == [root]

    def test_existing_pythonpath_wins_and_there_is_no_duplicate(self):
        root = os.path.dirname(_PKG)
        env = render_command.with_package_path({"PYTHONPATH": "a" + os.pathsep + "b"})
        assert env["PYTHONPATH"].split(os.pathsep) == ["a", "b", root]
        again = render_command.with_package_path(env)
        assert again["PYTHONPATH"] == env["PYTHONPATH"]


_PLAIN = '''"""A scene."""
from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        self.wait(1)
'''


class TestCodeguard:
    def test_short_scene_inherits_the_kit(self):
        paths.set_video_format("short")
        fixed = precheck_and_autofix(_PLAIN)
        assert "class Section01Scene(ShortScene):" in fixed
        assert fixed.count("from manimgen.scenekit import *") == 1
        assert fixed.index("from manimlib import *") < fixed.index("scenekit")
        assert inspect_scene_code(fixed).ok

    def test_landscape_scene_is_untouched(self):
        assert "ShortScene" not in precheck_and_autofix(_PLAIN)

    def test_already_kit_scene_and_strings_are_untouched(self):
        code = (
            "from manimlib import *\nfrom manimgen.scenekit import *\n\n\n"
            "class S(ShortScene):\n    def construct(self):\n"
            '        t = "class X(Scene):"\n        self.wait(1)\n'
        )
        assert _fix_short_scene_base(code) == (code, None)

    def test_import_is_added_even_without_a_manimlib_import_line(self):
        code = "class S(Scene):\n    def construct(self):\n        pass\n"
        fixed, label = _fix_short_scene_base(code)
        assert fixed.startswith("from manimgen.scenekit import *\n")
        assert "class S(ShortScene):" in fixed and label

    def test_unparseable_code_is_left_alone(self):
        assert _fix_short_scene_base("class S(Scene:\n") == ("class S(Scene:\n", None)

    def test_no_trailing_newline_after_the_import_line(self):
        fixed, _ = _fix_short_scene_base("from manimlib import *")
        assert fixed == "from manimlib import *"  # no class: nothing to do


class TestTiming:
    CODE = """from manimlib import *
from manimgen.scenekit import *


class S(ShortScene):
    def construct(self):
        # CUE 0 — 2.0s
        self.play(FadeIn(Square()), run_time=1.0)
        self.wait(1.0)
        # CUE 1 — 2.75s
        self.swap_key("a")
        self.wait(2.0)
        # CUE 2 — 1.5s
        self.swap_hook("b", run_time=0.25)
        self.drop(Square())
        self.wait(0.75)
"""

    def test_kit_calls_are_counted(self):
        result = verify_timing(self.CODE, [2.0, 2.75, 1.5])
        assert result["ok"], result
        assert [round(c.computed, 2) for c in result["cues"]] == [2.0, 2.75, 1.5]


class TestProbeGeometry:
    BOX = (0.0, 0.0, 1.0, 0.2)

    def shape(self, polys, fill=0.0, dark=False):
        xs = [x for p in polys for x, _ in p]
        ys = [y for p in polys for _, y in p]
        box = (min(xs) - 0.02, min(ys) - 0.02, max(xs) + 0.02, max(ys) + 0.02)
        return probe.ShapeItem("Shape", box, polys, fill, dark)

    def test_a_line_through_the_text_is_on_shape(self):
        line = self.shape([[(-0.5, 0.1), (1.5, 0.1)]])
        assert probe.on_shape_score(self.BOX, line) == 1.0

    def test_a_line_beside_the_text_is_not(self):
        line = self.shape([[(-0.5, 0.35), (1.5, 0.35)]])
        assert probe.on_shape_score(self.BOX, line) == 0.0

    def test_a_vertical_line_through_long_text_counts(self):
        line = self.shape([[(0.5, -1.0), (0.5, 1.0)]])
        assert probe.on_shape_score(self.BOX, line) >= 0.9

    def test_a_grazing_corner_does_not(self):
        line = self.shape([[(0.9, 0.19), (1.4, 0.5)]])
        assert probe.on_shape_score(self.BOX, line) == 0.0

    def square(self, fill, dark=False):
        return self.shape([[(-1, -1), (2, -1), (2, 1), (-1, 1), (-1, -1)]], fill, dark)

    def test_text_centred_in_a_faint_fill_is_ignored(self):
        assert probe.on_shape_score(self.BOX, self.square(0.25)) == 0.0

    def test_text_on_a_solid_light_fill_is_reported(self):
        assert probe.on_shape_score(self.BOX, self.square(1.0)) >= 0.5

    def test_text_on_a_dark_backing_plate_is_ignored(self):
        assert probe.on_shape_score(self.BOX, self.square(1.0, dark=True)) == 0.0

    def test_small_ratio(self):
        assert probe.small_ratio(0.21, 0.21) == pytest.approx(1.0)
        assert probe.small_ratio(0.1, 0.21) < probe.SMALL_RATIO
        assert probe.small_ratio(0.2, 0.0) is None

    def test_recorder_reports_small_and_on_shape(self):
        rec = probe.OverlapRecorder()
        small = probe.TextItem("tiny", (0, 0, 0.3, 0.1), obj=object())
        fine = probe.TextItem("fine", (0, 1, 0.3, 1.21), obj=object())
        heights = {id(small.obj): 0.21, id(fine.obj): 0.21}
        rec.observe_small(1.0, [small, fine], lambda item: heights[id(item.obj)])
        assert [(f.kind, f.a) for f in rec.findings] == [("small", "tiny")]
        rec.observe_shapes(
            2.0,
            [probe.TextItem("on a line", self.BOX)],
            [self.shape([[(-0.5, 0.1), (1.5, 0.1)]])],
        )
        assert rec.findings[-1].kind == "on_shape"
        # four small texts in a row are all kept (the per-text cap is by name)
        rec2 = probe.OverlapRecorder()
        items = [probe.TextItem(f"t{i}", (0, i, 0.3, i + 0.1), obj=i) for i in range(4)]
        rec2.observe_small(0.0, items, lambda item: 0.21)
        assert len(rec2.findings) == 4

    def test_report_text_for_the_new_kinds(self):
        small = overlap_report.Overlap(1.0, "tiny", "", 0.6, "small")
        on = overlap_report.Overlap(2.0, "4", "Square", 1.0, "on_shape")
        assert "too small" in overlap_report.format_issue(small)
        assert "font_size 22" in overlap_report.format_issue(small)
        assert "Square" in overlap_report.format_issue(on)
        assert overlap_report.format_issue(on).startswith(overlap_report.ISSUE_PREFIX)

    def test_report_parsing_keeps_the_kinds(self):
        data = {
            "findings": [
                {"time": 1, "a": "x", "b": "", "fraction": 0.6, "kind": "small"},
                {"time": 2, "a": "y", "b": "Line", "fraction": 1.0, "kind": "on_shape"},
            ]
        }
        report = overlap_report._parse(data)
        assert [o.kind for o in report.overlaps] == ["small", "on_shape"]
