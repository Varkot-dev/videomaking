"""R14 (#78): kwarg strips and renames must only touch the calls they target."""

import ast

import pytest

from manimgen.validator.codeguard import (
    apply_error_aware_fixes,
    apply_known_fixes,
    precheck_and_autofix,
)


def _known(code: str) -> str:
    return apply_known_fixes(code)[0]


class TestBannedKwargScope:
    def test_scale_factor_variable_assignment_survives(self):
        code = "scale_factor = 1.5\nsq.scale(scale_factor)\n"
        assert _known(code) == code

    def test_scale_factor_in_comparison_survives(self):
        code = "if scale_factor == 2:\n    pass\n"
        out = _known(code)
        assert out == code
        ast.parse(out)

    def test_indicate_keeps_its_valid_scale_factor(self):
        code = "self.play(Indicate(sq, color=YELLOW, scale_factor=1.05))\n"
        assert _known(code) == code

    def test_fade_scale_factor_still_stripped(self):
        out = _known("self.play(FadeIn(obj, scale_factor=1.5))\n")
        assert out == "self.play(FadeIn(obj))\n"

    def test_rounded_rectangle_keeps_corner_radius(self):
        code = "r = RoundedRectangle(width=2, height=1, corner_radius=0.2)\n"
        assert _known(code) == code

    def test_rectangle_corner_radius_still_stripped(self):
        out = _known("r = Rectangle(width=2, height=1, corner_radius=0.2)\n")
        assert out == "r = Rectangle(width=2, height=1)\n"

    def test_tip_length_only_stripped_from_arrows(self):
        code = "nl = NumberLine(tip_length=0.2)\nf(tip_length=1)\n"
        assert _known(code) == code
        assert _known("a = Arrow(LEFT, RIGHT, tip_length=0.3)\n") == (
            "a = Arrow(LEFT, RIGHT)\n"
        )

    def test_value_with_commas_and_parens_is_removed_whole(self):
        out = _known("a = Arrow(LEFT, RIGHT, tip_shape=Foo(1, 2), buff=0)\n")
        assert out == "a = Arrow(LEFT, RIGHT, buff=0)\n"

    def test_multiline_strip_keeps_comments(self):
        code = (
            "s = Surface(\n"
            "    f,  # the function\n"
            "    checkerboard_colors=[BLUE_D, BLUE_E],\n"
            "    v_range=[-2, 2],\n"
            ")\n"
        )
        out = _known(code)
        assert "# the function" in out
        assert "checkerboard_colors" not in out
        assert "v_range=[-2, 2]" in out
        ast.parse(out)

    def test_non_ascii_before_the_kwarg(self):
        out = _known('a = [Text("ππ"), Arrow(LEFT, RIGHT, tip_length=0.3)]\n')
        assert out == 'a = [Text("ππ"), Arrow(LEFT, RIGHT)]\n'

    def test_unparseable_code_is_left_alone(self):
        code = "a = Arrow(LEFT, tip_length=0.3\n"
        assert _known(code) == code


class TestAxesLengthRename:
    def test_variable_named_x_length_survives(self):
        code = "x_length = 8\nprint(x_length)\n"
        assert _known(code) == code

    def test_other_calls_keep_length_kwargs(self):
        code = "foo(x_length=3)\nl = Line(UP, DOWN, z_length=1)\n"
        assert _known(code) == code

    def test_axes_kwargs_renamed(self):
        out = _known("ax = Axes(x_length=8, y_length=4)\n")
        assert out == "ax = Axes(width=8, height=4)\n"

    def test_threed_axes_z_length_renamed(self):
        out = _known("ax = ThreeDAxes(z_length=3)\n")
        assert out == "ax = ThreeDAxes(depth=3)\n"

    def test_rename_never_duplicates_a_kwarg(self):
        code = "ax = Axes(x_length=8, width=9)\n"
        assert _known(code) == code


class TestErrorAwareScope:
    def test_constructor_error_names_the_class_not_init(self):
        code = (
            "self.play(Indicate(sq, scale_factor=1.05))\n"
            "r = Rectangle(corner_radius=0.2)\n"
        )
        stderr = (
            "TypeError: Rectangle.__init__() got an unexpected keyword argument "
            "'corner_radius'"
        )
        fixed, _ = apply_error_aware_fixes(code, stderr)
        assert "Rectangle()" in fixed

    def test_strip_does_not_touch_same_named_kwarg_elsewhere(self):
        code = "a = Foo(opacity=1)\nb = Bar(opacity=2)\n"
        stderr = (
            "TypeError: Bar.__init__() got an unexpected keyword argument 'opacity'"
        )
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert fixed == "a = Foo(opacity=1)\nb = Bar()\n"
        assert applied

    def test_rename_does_not_touch_same_named_kwarg_elsewhere(self):
        code = "a = Foo(size=1)\nb = Bar(size=2)\n"
        stderr = (
            "TypeError: Bar.__init__() got an unexpected keyword argument 'size'. "
            "Did you mean 'sz'?"
        )
        fixed, _ = apply_error_aware_fixes(code, stderr)
        assert fixed == "a = Foo(size=1)\nb = Bar(sz=2)\n"

    def test_base_class_error_pinned_by_traceback_line(self):
        code = "a = Foo(opacity=1)\nb = Bar(opacity=2)\n"
        stderr = (
            'File "scene.py", line 2, in construct\n'
            "TypeError: Mobject.__init__() got an unexpected keyword argument 'opacity'"
        )
        fixed, _ = apply_error_aware_fixes(code, stderr)
        assert fixed == "a = Foo(opacity=1)\nb = Bar()\n"

    def test_ambiguous_base_class_error_changes_nothing(self):
        code = "a = Foo(opacity=1)\nb = Bar(opacity=2)\n"
        stderr = (
            "TypeError: Mobject.__init__() got an unexpected keyword argument 'opacity'"
        )
        assert apply_error_aware_fixes(code, stderr)[0] == code

    def test_numberline_label_with_parens_stays_balanced(self):
        code = 'nl = NumberLine(x_range=[0, 1], label=Tex("f(x)"), include_tip=True)\n'
        stderr = (
            "TypeError: NumberLine.__init__() got an unexpected keyword argument "
            "'label'"
        )
        fixed, _ = apply_error_aware_fixes(code, stderr)
        assert fixed == "nl = NumberLine(x_range=[0, 1], include_tip=True)\n"

    def test_registry_scopes_to_the_failing_method(self):
        code = "g.arrange_in_grid(rows=2)\nh.other(rows=3)\n"
        stderr = (
            "TypeError: Mobject.arrange_in_grid() got an unexpected keyword "
            "argument 'rows'"
        )
        fixed, _ = apply_error_aware_fixes(code, stderr)
        assert fixed == "g.arrange_in_grid(n_rows=2)\nh.other(rows=3)\n"

    def test_name_remap_uses_whole_identifiers(self):
        code = "a = DARK_GREY\nb = DARK_GREY_X\n"
        fixed, _ = apply_error_aware_fixes(
            code, "NameError: name 'DARK_GREY' is not defined"
        )
        assert fixed == "a = GREY_D\nb = DARK_GREY_X\n"

    @pytest.mark.parametrize("name", ["TEAL", "MAROON", "PURPLE", "PINK", "DARK_BROWN"])
    def test_names_manimgl_defines_are_not_remapped(self, name):
        code = f"c = {name}_A\nd = {name}\n"
        fixed, _ = apply_error_aware_fixes(
            code, f"NameError: name '{name}' is not defined"
        )
        assert fixed == code


def test_valid_scene_with_indicate_scale_factor_survives_precheck():
    code = (
        "from manimlib import *\n\n\n"
        "class S(Scene):\n"
        "    def construct(self):\n"
        "        sq = Square()\n"
        "        scale_factor = 1.5\n"
        "        self.play(Indicate(sq, color=YELLOW, scale_factor=1.05))\n"
        "        sq.scale(scale_factor)\n"
        "        if scale_factor == 2:\n"
        "            x_length = 8\n"
    )
    assert precheck_and_autofix(code) == code


class TestIndicateScaleFactorAgreesAcrossValidators:
    """manimlib 1.7.2 Indicate.__init__ takes scale_factor; only FadeIn/FadeOut reject it."""

    def test_validate_scene_code_accepts_indicate_scale_factor(self):
        from manimgen.validator.codeguard import validate_scene_code

        code = "self.play(Indicate(sq, color=YELLOW, scale_factor=1.05))\n"
        assert not any("scale_factor" in e for e in validate_scene_code(code))

    def test_validate_scene_code_still_rejects_fade_scale_factor(self):
        from manimgen.validator.codeguard import validate_scene_code

        for call in ("FadeIn", "FadeOut"):
            errs = validate_scene_code(f"self.play({call}(sq, scale_factor=1.5))\n")
            assert any("scale_factor" in e for e in errs), call

    def test_text_reveal_example_survives_codeguard_untouched(self):
        from pathlib import Path

        import manimgen
        from manimgen.validator.codeguard import apply_known_fixes

        src = (
            Path(manimgen.__file__).parent / "examples" / "text_reveal_scene.py"
        ).read_text(encoding="utf-8")
        assert "scale_factor=1.05" in src
        fixed, applied = apply_known_fixes(src)
        assert "scale_factor=1.05" in fixed
        assert not any("scale_factor" in a for a in applied)
