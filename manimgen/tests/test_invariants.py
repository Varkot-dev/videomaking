"""Tests for the design-system invariant registry (validator/invariants.py).

Salvaged and adapted from the pre-refactor codeguard test suite. The original
tests targeted `codeguard.run_invariant_warnings()`; the invariant logic has
since been extracted into the data-driven `invariants.py` registry exposing
`run_all(code) -> (errors, warnings)`. These tests call that registry directly
so the I-series checks regain coverage after the module move.

Warning message prefixes (I2:, I3:, I4:, I5:, I7:, I9:) are part of the
invariant contract — the retry LLM keys off them — so asserting on the prefix
is intentional, not brittle.
"""

from manimgen.validator.invariants import run_all


def _warnings(code: str) -> list[str]:
    """Run the registry and return only the warning channel."""
    _errors, warnings = run_all(code)
    return warnings


# ── I3 — raw hex colors outside the sanctioned palette ───────────────────────


class TestI3RawHexWarning:
    """I3 warns on raw hex literals so the Director uses palette role constants.

    (The pre-refactor suite tested an apply_known_fixes hex→constant autofix.
    The current design surfaces raw hex as an I3 warning instead of silently
    rewriting it, so these assert on the warning.)
    """

    def test_raw_hex_warns(self):
        code = 'curve = axes.get_graph(lambda x: x, color="#00D9FF")'
        assert any(w.startswith("I3:") and "#00D9FF" in w for w in _warnings(code))

    def test_secondary_hex_warns(self):
        code = 'dot = Dot(color="#FF6B35")'
        assert any(w.startswith("I3:") and "#FF6B35" in w for w in _warnings(code))

    def test_sanctioned_bg_hex_not_warned(self):
        # #1C1C1C is the render background — design-system-sanctioned, no warning.
        code = 'rect = Rectangle(fill_color="#1C1C1C")'
        assert not any("#1C1C1C" in w for w in _warnings(code))

    def test_each_distinct_hex_warns_once(self):
        code = (
            'curve = axes.get_graph(lambda x: x, color="#00D9FF")\n'
            'area = axes.get_area(curve, color="#FF6B35")\n'
        )
        i3 = [w for w in _warnings(code) if w.startswith("I3:")]
        assert any("#00D9FF" in w for w in i3)
        assert any("#FF6B35" in w for w in i3)


# ── I4 — font_size off the canonical type scale ──────────────────────────────


class TestI4FontSizeCanonicalScale:
    """I4 warns on font_size literals outside the canonical scale."""

    def test_valid_font_sizes_no_warning(self):
        code = (
            'title = Text("Hello", font_size=48)\n'
            'sub = Text("world", font_size=28)\n'
            'caption = Text("note", font_size=18)\n'
        )
        assert not any(w.startswith("I4:") for w in _warnings(code))

    def test_off_scale_font_size_warns(self):
        code = 'label = Text("hi", font_size=32)'
        assert any("font_size=32" in w and "I4" in w for w in _warnings(code))

    def test_all_canonical_sizes_no_warning(self):
        for size in (48, 44, 36, 28, 22, 20, 18):
            code = f'label = Text("x", font_size={size})'
            assert not any(
                w.startswith("I4:") for w in _warnings(code)
            ), f"font_size={size} should not warn"

    def test_font_size_24_warns(self):
        code = 'tick = Text("0", font_size=24)'
        assert any("font_size=24" in w for w in _warnings(code))


# ── I5 — every scene must end with a full FadeOut of self.mobjects ────────────


class TestI5FadeOutCleanup:
    def _i5(self, code: str) -> list[str]:
        return [w for w in _warnings(code) if "I5" in w]

    def test_no_fadeout_at_all_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('hello')\n"
            "        self.play(FadeIn(t))\n"
            "        self.wait(1)\n"
        )
        assert self._i5(code), "Expected I5 warning when no FadeOut present"

    def test_full_fadeout_pattern_at_tail_no_warn(self):
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('hello')\n"
            "        self.play(FadeIn(t))\n"
            "        self.wait(1)\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not self._i5(code), "Expected no I5 warning with full FadeOut cleanup"

    def test_fadeout_without_self_mobjects_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('hello')\n"
            "        self.play(FadeIn(t))\n"
            "        self.wait(1)\n"
            "        self.play(FadeOut(t))\n"
        )
        assert self._i5(code), "Expected I5 warning when FadeOut lacks self.mobjects"

    def test_full_fadeout_buried_deep_warns(self):
        body_lines = ["        self.wait(0.1)\n"] * 20
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
            + "".join(body_lines)
        )
        assert self._i5(code), (
            "Expected I5 warning when full FadeOut is not in the last 15 lines"
        )


# ── I7 — ThreeDScene Text/Tex must call .fix_in_frame() within 5 lines ────────


class TestI7ThreeDFixInFrame:
    def _i7(self, code: str) -> list[str]:
        return [w for w in _warnings(code) if "I7" in w]

    def test_threedscene_text_missing_fix_in_frame_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        label = Text('hello')\n"
            "        self.play(FadeIn(label))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        w = self._i7(code)
        assert w, "Expected I7 warning for Text without fix_in_frame"
        assert "label" in w[0]

    def test_threedscene_text_with_fix_in_frame_no_warn(self):
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        label = Text('hello')\n"
            "        label.fix_in_frame()\n"
            "        self.play(FadeIn(label))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not self._i7(code), "Expected no I7 warning when fix_in_frame is present"

    def test_regular_scene_text_no_warn(self):
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        label = Text('hello')\n"
            "        self.play(FadeIn(label))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not self._i7(code), "Expected no I7 warning for plain Scene"

    def test_threedscene_tex_missing_fix_in_frame_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        eq = Tex(r'x^2')\n"
            "        self.play(Write(eq))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        w = self._i7(code)
        assert w, "Expected I7 warning for Tex without fix_in_frame"
        assert "eq" in w[0]

    def test_inline_text_not_warned(self):
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        self.play(FadeIn(Text('hello')))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not self._i7(code), (
            "Expected no I7 warning for inline Text (no variable assignment)"
        )


# ── I9 — high mobject density heuristic (> 10 distinct creations) ─────────────


class TestI9DensityWarning:
    _MOBJECT_TYPES = (
        "Text", "Tex", "Dot", "Circle", "Arrow",
        "Line", "Rectangle", "VGroup", "Axes", "NumberLine",
        "ParametricSurface",
    )

    def _make_code(self, n: int) -> str:
        """A scene with n distinct mobject creations plus a tail FadeOut."""
        lines = [
            f"    obj{i} = {self._MOBJECT_TYPES[i % len(self._MOBJECT_TYPES)]}()"
            for i in range(n)
        ]
        lines.append(
            "    self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)"
        )
        return "\n".join(lines)

    def test_11_objects_warns(self):
        warnings = _warnings(self._make_code(11))
        assert any("I9" in w for w in warnings), (
            f"Expected I9 density warning for 11 objects, got: {warnings}"
        )
        assert any("11" in w for w in warnings)

    def test_5_objects_no_warn(self):
        assert not any("I9" in w for w in _warnings(self._make_code(5)))

    def test_exactly_10_objects_no_warn(self):
        # Boundary: threshold is > 10, so exactly 10 must not warn.
        assert not any("I9" in w for w in _warnings(self._make_code(10)))

    def test_warning_mentions_vgroup_suggestion(self):
        assert any("VGroup" in w for w in _warnings(self._make_code(12)))


# ── I2 — zone grammar: .to_edge(UP) reserved for the section title ────────────


class TestI2ZoneGrammar:
    def test_single_to_edge_up_no_warn(self):
        code = (
            "title = Text('Section').to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not any(
            "I2" in w and "Multiple" in w for w in _warnings(code)
        ), "Single to_edge(UP) should not warn"

    def test_double_to_edge_up_warns(self):
        code = (
            "title = Text('Section').to_edge(UP)\n"
            "subtitle = Text('Sub').to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert any(
            "I2" in w and "Multiple" in w for w in _warnings(code)
        ), "Expected I2 multiple-to_edge(UP) warning"

    def test_axes_to_edge_up_warns(self):
        code = (
            "axes = Axes(x_range=[-3, 3], y_range=[-2, 2])\n"
            "axes.to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert any(
            "I2" in w and "axes" in w.lower() for w in _warnings(code)
        ), "Expected I2 content-in-title-zone warning for axes.to_edge(UP)"

    def test_curve_to_edge_up_warns(self):
        code = (
            "curve = axes.get_graph(lambda x: x**2)\n"
            "curve.to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert any("I2" in w for w in _warnings(code)), (
            "Expected I2 warning for curve.to_edge(UP)"
        )

    def test_to_edge_down_not_flagged(self):
        code = (
            "footer = Text('note').to_edge(DOWN)\n"
            "footer2 = Text('note2').to_edge(DOWN)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not any("I2" in w for w in _warnings(code)), (
            "to_edge(DOWN) should not trigger I2"
        )
