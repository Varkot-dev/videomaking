"""Pure geometry of the Shorts layout kit: candidate placement and the font floor.

``scenekit/shorts.py`` imports manimlib at the top, so it is loaded here under a
stub ``manimlib`` module: no GL, no display, runs everywhere.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

import manimgen

_SHORTS = Path(manimgen.__file__).parent / "scenekit" / "shorts.py"


@pytest.fixture(scope="module")
def kit():
    stub = types.ModuleType("manimlib")
    for name in (
        "StringMobject",
        "DecimalNumber",
        "Brace",
        "Checkmark",
        "Exmark",
        "Scene",
    ):
        setattr(stub, name, type(name, (), {}))
    stub.GOLD = stub.WHITE = "#ffffff"  # used as default arguments
    saved = sys.modules.get("manimlib")
    sys.modules["manimlib"] = stub
    try:
        spec = importlib.util.spec_from_file_location("_kit_under_test", _SHORTS)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        if saved is None:
            del sys.modules["manimlib"]
        else:
            sys.modules["manimlib"] = saved
    return mod


SQUARE = [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5), (-0.5, -0.5)]


def closed_info(fill):
    return {
        "kind": "closed",
        "box": (-0.5, -0.5, 0.5, 0.5),
        "polys": [SQUARE],
        "idents": {1},
        "fill": fill,
    }


def shape(fill=0.0, polys=None, ident=1):
    polys = polys or [SQUARE]
    xs = [x for p in polys for x, _ in p]
    ys = [y for p in polys for _, y in p]
    return {
        "box": (min(xs), min(ys), max(xs), max(ys)),
        "polys": polys,
        "fill": fill,
        "ident": ident,
    }


@pytest.mark.unit
class TestPrimitives:
    def test_segment_clipping(self, kit):
        box = (0, 0, 2, 2)
        assert kit.clip_segment_length((-1, 1), (3, 1), box) == pytest.approx(2.0)
        assert kit.clip_segment_length((3, 3), (4, 4), box) == 0.0
        assert kit.clip_segment_length((0, 3), (2, 3), box) == 0.0

    def test_point_in_polys_respects_holes(self, kit):
        outer = [(0, 0), (4, 0), (4, 4), (0, 4)]
        hole = [(1, 1), (3, 1), (3, 3), (1, 3)]
        assert kit.point_in_polys(0.5, 0.5, [outer, hole])
        assert not kit.point_in_polys(2, 2, [outer, hole])

    def test_share_outside_and_overlap(self, kit):
        assert kit.share_outside((0, 0, 1, 1), (0, 0, 2, 2)) == 0.0
        assert kit.share_outside((1, 0, 3, 1), (0, 0, 2, 2)) == pytest.approx(0.5)
        assert kit.box_overlap_area((0, 0, 2, 2), (1, 1, 3, 3)) == pytest.approx(1.0)

    def test_fit_scale_uses_the_tighter_axis(self, kit):
        band = (-2.0, -1.4, 1.4, 1.4)  # 3.4 wide, 2.8 tall
        assert kit.fit_scale(7, 7, band, (0, 0, 0, 0)) == pytest.approx(0.4)
        assert kit.fit_scale(7, 7, band, (0.4, 0.4, 0.4, 0.4)) == pytest.approx(2.0 / 7)
        assert kit.fit_scale(1, 7, band, (0, 0, 0, 0)) == pytest.approx(0.4)

    def test_wrapping_splits_long_text_only(self, kit):
        assert kit.split_lines("short", 1.0, 3.3) == ["short"]
        lines = kit.split_lines("why is the sum of nine and sixteen so", 6.0, 3.3)
        assert len(lines) >= 2 and all(len(x) <= 21 for x in lines)
        assert kit.split_lines("unbreakable", 9.0, 3.3) == ["unbreakable"]


@pytest.mark.unit
class TestFontFloor:
    @pytest.mark.parametrize(
        "asked, got",
        [
            (10, 22),
            (18, 22),
            (20, 22),
            (22, 22),
            (25, 22),
            (28, 28),
            (30, 28),
            (36, 36),
            (99, 36),
        ],
    )
    def test_snap_never_goes_below_22(self, kit, asked, got):
        assert kit.snap_font(asked) == got

    def test_constants_keep_the_floor_and_the_allowed_scale(self, kit):
        assert kit.MIN_FONT == 22
        assert min(kit.FONT_STEPS) == kit.MIN_FONT
        assert set(kit.FONT_STEPS) == {22, 28, 36}


@pytest.mark.unit
class TestCandidates:
    W, H = 0.3, 0.2
    DIAGRAM = (-1.0, -1.0, 1.0, 1.0)

    def names(self, kit, info, prefer=None):
        found = kit.label_candidates(info, self.W, self.H, 0.12, prefer, self.DIAGRAM)
        order = []
        for name, _x, _y in found:
            if name not in order:
                order.append(name)
        return order

    def test_closed_faint_fill_tries_inside_first_then_the_four_sides(self, kit):
        order = self.names(kit, closed_info(0.2))
        assert order[0] == "inside"
        assert [n for n in order if n != "inside"] == [
            "right",
            "left",
            "above",
            "below",
        ]

    def test_inside_is_skipped_for_a_strong_fill(self, kit):
        assert "inside" not in self.names(kit, closed_info(0.6))

    def test_prefer_moves_a_side_to_the_front(self, kit):
        assert self.names(kit, closed_info(0.2), "below")[0] == "below"
        assert self.names(kit, closed_info(0.0), "inside")[0] == "inside"

    def test_a_target_below_the_diagram_prefers_below(self, kit):
        brace = {
            "kind": "open",
            "box": (-0.5, -1.4, 0.5, -1.2),
            "polys": [],
            "idents": set(),
            "fill": 1.0,
        }
        assert self.names(kit, brace)[0] == "below"

    def test_line_gets_outward_normal_first(self, kit):
        info = {
            "kind": "line",
            "box": (-0.5, 1.0, 0.5, 1.0),
            "ends": ((-0.5, 1.0), (0.5, 1.0)),
            "polys": [],
            "idents": set(),
            "fill": 0.0,
        }
        found = kit.label_candidates(info, self.W, self.H, 0.12, None, self.DIAGRAM)
        assert found[0][0] == "out" and found[0][2] > 1.0  # above the line
        assert {n for n, _, _ in found} == {"out", "in"}

    def test_inside_spot_of_a_triangle_is_inside_it(self, kit):
        tri = [(0, 0), (4, 0), (0, 3), (0, 0)]
        info = {"kind": "closed", "box": (0, 0, 4, 3), "polys": [tri], "fill": 0.0}
        spots = kit.inside_positions(info, 0.3, 0.2)
        assert spots
        assert all(kit.point_in_polys(x, y, [tri]) for _, x, y in spots)


@pytest.mark.unit
class TestViolations:
    W, H = 0.3, 0.2

    def check(self, kit, cand, shapes=(), texts=(), inside=False):
        return kit.candidate_violations(
            cand, self.W, self.H, list(shapes), list(texts), {1}, inside
        )

    def test_a_clear_spot_is_good(self, kit):
        v = self.check(kit, (0.9, 0.0), [shape()])
        assert kit.is_good(v)

    def test_leaving_the_label_area_is_bad(self, kit):
        v = self.check(kit, (0.0, -1.35))  # bottom edge below -1.4
        assert v["outside"] > 0 and not kit.is_good(v)
        v = self.check(kit, (1.3, 0.0))  # right edge past 1.4
        assert not kit.is_good(v)

    def test_covering_another_text_is_bad(self, kit):
        other = (0.8, -0.1, 1.1, 0.1)
        assert not kit.is_good(self.check(kit, (0.95, 0.0), texts=[other]))

    def test_crossing_an_outline_is_bad_but_inside_a_shape_is_fine(self, kit):
        on_edge = self.check(kit, (0.5, 0.0), [shape(0.2)])
        assert on_edge["cross"] > kit.TOL_CROSS
        centred = self.check(kit, (0.0, 0.0), [shape(0.2)], inside=True)
        assert kit.is_good(centred)

    def test_sitting_on_another_filled_shape_is_bad(self, kit):
        other = shape(0.25, ident=2)
        v = kit.candidate_violations(
            (0.0, 0.0), self.W, self.H, [other], [], {1}, False
        )
        assert v["fill"] > kit.TOL_FILL

    def test_least_bad_ranks_by_severity(self, kit):
        light = {"outside": 0.0, "text": 0.0, "cross": 0.1, "fill": 0.0}
        heavy = {"outside": 0.0, "text": 0.5, "cross": 0.0, "fill": 0.0}
        assert kit.badness(light) < kit.badness(heavy)
        assert "crosses a line" in kit.violation_note(light)

    def test_choosing_the_first_good_candidate(self, kit):
        """With the right side taken by another label, the left side wins."""
        info = closed_info(0.6)  # strong fill: no inside spot
        blocker = (0.55, -0.8, 0.95, 0.8)
        cands = kit.label_candidates(info, self.W, self.H, 0.12, None, (-1, -1, 1, 1))
        chosen = None
        for name, x, y in cands:
            v = kit.candidate_violations(
                (x, y), self.W, self.H, [shape(0.6)], [blocker], {1}, False
            )
            if kit.is_good(v):
                chosen = name
                break
        assert chosen not in (None, "right")
