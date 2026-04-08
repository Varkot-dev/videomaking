"""
Tests for manimgen/validator/codeguard.py

Covers every auto-fix, every banned pattern detection, and the
error-aware repair path. Zero LLM calls, zero subprocess calls.
"""

import pytest
from manimgen.validator.codeguard import (
    apply_known_fixes,
    apply_error_aware_fixes,
    validate_scene_code,
    _fix_color_gradient_int_cast,
    _remove_font_kwarg_from_tex,
)


# ── apply_known_fixes ─────────────────────────────────────────────────────────

class TestApplyKnownFixes:

    def test_fixes_wrong_import(self):
        code = "from manim import *\nclass Foo(Scene): pass"
        fixed, applied = apply_known_fixes(code)
        assert "from manimlib import *" in fixed
        assert "from manim import *" not in fixed
        assert any("fixed import" in a for a in applied)

    def test_fixes_mathtex(self):
        code = "eq = MathTex(r'x^2')"
        fixed, applied = apply_known_fixes(code)
        assert "Tex(" in fixed
        assert "MathTex" not in fixed
        assert any("MathTex" in a for a in applied)

    def test_fixes_create(self):
        code = "self.play(Create(circle))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(circle)" in fixed
        assert "Create(circle)" not in fixed

    def test_fixes_camera_frame(self):
        code = "self.camera.frame.move_to(UP)"
        fixed, applied = apply_known_fixes(code)
        assert "self.frame.move_to" in fixed
        assert "self.camera.frame" not in fixed

    def test_fixes_circumscribe(self):
        code = "self.play(Circumscribe(obj))"
        fixed, applied = apply_known_fixes(code)
        assert "FlashAround(obj)" in fixed
        assert "Circumscribe" not in fixed

    def test_removes_tip_length(self):
        code = "Arrow(LEFT, RIGHT, tip_length=0.3)"
        fixed, applied = apply_known_fixes(code)
        assert "tip_length" not in fixed
        assert any("tip_length" in a for a in applied)

    def test_removes_tip_width(self):
        code = "Arrow(LEFT, RIGHT, tip_width=0.2)"
        fixed, applied = apply_known_fixes(code)
        assert "tip_width" not in fixed

    def test_removes_tip_shape(self):
        code = "Arrow(LEFT, RIGHT, tip_shape=ArrowTriangleTip)"
        fixed, applied = apply_known_fixes(code)
        assert "tip_shape" not in fixed

    def test_removes_corner_radius(self):
        code = "SurroundingRectangle(obj, corner_radius=0.2)"
        fixed, applied = apply_known_fixes(code)
        assert "corner_radius" not in fixed

    def test_removes_scale_factor(self):
        code = "self.play(FadeIn(obj, scale_factor=1.5))"
        fixed, applied = apply_known_fixes(code)
        assert "scale_factor" not in fixed
        assert any("scale_factor" in a for a in applied)

    def test_removes_target_position(self):
        code = "obj.move_to(ORIGIN, target_position=UP)"
        fixed, applied = apply_known_fixes(code)
        assert "target_position" not in fixed

    def test_fixes_dark_grey(self):
        code = "circle = Circle(color=DARK_GREY)"
        fixed, _ = apply_known_fixes(code)
        assert "GREY_D" in fixed
        assert "DARK_GREY" not in fixed

    def test_fixes_dark_gray(self):
        code = "circle = Circle(color=DARK_GRAY)"
        fixed, _ = apply_known_fixes(code)
        assert "GREY_D" in fixed

    def test_fixes_dark_blue(self):
        code = "text = Text('hi', color=DARK_BLUE)"
        fixed, _ = apply_known_fixes(code)
        assert "BLUE_D" in fixed

    def test_fixes_dark_green(self):
        fixed, _ = apply_known_fixes("Circle(color=DARK_GREEN)")
        assert "GREEN_D" in fixed

    def test_fixes_dark_red(self):
        fixed, _ = apply_known_fixes("Circle(color=DARK_RED)")
        assert "RED_D" in fixed

    def test_fixes_light_grey(self):
        fixed, _ = apply_known_fixes("Circle(color=LIGHT_GREY)")
        assert "GREY_A" in fixed

    def test_fixes_light_gray(self):
        fixed, _ = apply_known_fixes("Circle(color=LIGHT_GRAY)")
        assert "GREY_A" in fixed

    def test_fixes_zero_length_arrow(self):
        code = "Arrow(ORIGIN, ORIGIN)"
        fixed, applied = apply_known_fixes(code)
        assert "Arrow(ORIGIN, ORIGIN)" not in fixed
        assert any("zero-length Arrow" in a for a in applied)

    def test_fixes_zero_length_arrow_with_kwargs(self):
        code = "Arrow(ORIGIN, ORIGIN, color=RED)"
        fixed, applied = apply_known_fixes(code)
        assert "Arrow(ORIGIN, ORIGIN" not in fixed

    def test_color_gradient_int_cast_literal(self):
        code = "color_gradient([RED, BLUE], 5.0)"
        fixed, applied = apply_known_fixes(code)
        assert "int(5.0)" in fixed
        assert any("color_gradient" in a for a in applied)

    def test_color_gradient_int_cast_variable(self):
        code = "color_gradient([RED, BLUE], n)"
        fixed, applied = apply_known_fixes(code)
        assert "int(n)" in fixed

    def test_color_gradient_already_int_not_double_wrapped(self):
        code = "color_gradient([RED, BLUE], int(n))"
        fixed, applied = apply_known_fixes(code)
        assert fixed.count("int(") == 1

    def test_removes_font_from_tex(self):
        code = 'eq = Tex(r"x^2", font="Arial")'
        fixed, applied = apply_known_fixes(code)
        assert 'font="Arial"' not in fixed
        assert any("font" in a for a in applied)

    def test_removes_font_from_textext(self):
        code = 'label = TexText("hello", font="Helvetica")'
        fixed, applied = apply_known_fixes(code)
        assert 'font=' not in fixed

    def test_does_not_remove_font_from_text(self):
        code = 'label = Text("hello", font="Arial")'
        fixed, applied = apply_known_fixes(code)
        assert 'font="Arial"' in fixed

    def test_no_changes_returns_empty_applied(self):
        code = "from manimlib import *\nclass Foo(Scene): pass"
        fixed, applied = apply_known_fixes(code)
        assert fixed == code
        assert applied == []

    def test_multiple_fixes_in_one_file(self):
        code = (
            "from manim import *\n"
            "MathTex('x')\n"
            "Create(circle)\n"
            "DARK_GREY\n"
        )
        fixed, applied = apply_known_fixes(code)
        assert "from manimlib import *" in fixed
        assert "Tex(" in fixed
        assert "ShowCreation(" in fixed
        assert "GREY_D" in fixed
        assert len(applied) >= 4


# ── validate_scene_code ───────────────────────────────────────────────────────

class TestValidateSceneCode:

    def test_valid_code_no_errors(self):
        code = "from manimlib import *\nclass Foo(Scene):\n    def construct(self): pass"
        errors = validate_scene_code(code)
        assert errors == []

    def test_detects_wrong_import(self):
        code = "from manim import *"
        errors = validate_scene_code(code)
        assert any("manimlib" in e for e in errors)

    def test_detects_mathtex(self):
        errors = validate_scene_code("MathTex('x')")
        assert any("MathTex" in e for e in errors)

    def test_detects_create(self):
        errors = validate_scene_code("Create(circle)")
        assert any("ShowCreation" in e for e in errors)

    def test_detects_scale_factor(self):
        errors = validate_scene_code("FadeIn(obj, scale_factor=1.5)")
        assert any("scale_factor" in e for e in errors)

    def test_detects_circumscribe(self):
        errors = validate_scene_code("Circumscribe(obj)")
        assert any("FlashAround" in e for e in errors)

    def test_detects_syntax_error(self):
        errors = validate_scene_code("def foo(:\n    pass")
        assert any("SyntaxError" in e for e in errors)

    def test_detects_camera_frame(self):
        errors = validate_scene_code("self.camera.frame.move_to(UP)")
        assert any("self.frame" in e for e in errors)

    def test_detects_zero_length_arrow(self):
        errors = validate_scene_code("Arrow(ORIGIN, ORIGIN)")
        assert any("same point" in e for e in errors)

    def test_multiple_errors_all_reported(self):
        code = "from manim import *\nMathTex('x')\nCreate(c)"
        errors = validate_scene_code(code)
        assert len(errors) >= 3


# ── apply_error_aware_fixes ───────────────────────────────────────────────────

class TestApplyErrorAwareFixes:

    def test_latex_not_found_tex_str(self):
        code = "label = Tex(str(n))"
        stderr = "No such file or directory: 'latex'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "Text(str(n))" in fixed
        assert any("Text" in a for a in applied)

    def test_latex_not_found_tex_numeric_literal(self):
        code = 'label = Tex("42")'
        stderr = "No such file or directory: 'latex'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert 'Text("42")' in fixed

    def test_unexpected_kwarg_from_stderr(self):
        code = "FadeIn(obj, run_time=1, bogus_param=True)"
        stderr = "TypeError: Animation.__init__() got an unexpected keyword argument 'bogus_param'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "bogus_param" not in fixed
        assert any("bogus_param" in a for a in applied)

    def test_name_error_dark_grey(self):
        code = "Circle(color=DARK_GREY)"
        stderr = "NameError: name 'DARK_GREY' is not defined"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "GREY_D" in fixed

    def test_name_error_dark_blue(self):
        code = "Circle(color=DARK_BLUE)"
        stderr = "NameError: name 'DARK_BLUE' is not defined"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "BLUE_D" in fixed

    def test_name_error_light_grey(self):
        code = "Circle(color=LIGHT_GREY)"
        stderr = "NameError: name 'LIGHT_GREY' is not defined"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "GREY_A" in fixed

    def test_color_gradient_type_error(self):
        code = "color_gradient([RED, BLUE], n)"
        stderr = "TypeError: color_gradient failed\n'float' object cannot be interpreted as an integer"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "int(n)" in fixed

    def test_no_changes_on_unrecognized_error(self):
        code = "from manimlib import *"
        stderr = "some random unrecognized error"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert fixed == code
        assert applied == []

    def test_latex_not_found_alternative_message(self):
        code = "label = Tex(str(x))"
        stderr = "latex: not found"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "Text(str(x))" in fixed


# ── _fix_color_gradient_int_cast ─────────────────────────────────────────────

class TestColorGradientIntCast:

    def test_float_literal(self):
        code = "color_gradient([RED, BLUE], 10.0)"
        fixed, label = _fix_color_gradient_int_cast(code)
        assert "int(10.0)" in fixed
        assert label is not None

    def test_integer_literal_not_wrapped(self):
        # integer literal passed as float string - still wraps
        code = "color_gradient([RED, BLUE], 5)"
        fixed, label = _fix_color_gradient_int_cast(code)
        # 5 is not a float, ValueError raised, so wraps with int()
        assert "int(5)" in fixed

    def test_already_wrapped_not_double_wrapped(self):
        # int() is already present — the value should not be double-wrapped
        code = "color_gradient([RED, BLUE], int(n))"
        fixed, label = _fix_color_gradient_int_cast(code)
        assert fixed.count("int(") == 1  # still exactly one int() wrap

    def test_variable_wrapped(self):
        code = "color_gradient(colors, length)"
        fixed, label = _fix_color_gradient_int_cast(code)
        assert "int(length)" in fixed


# ── _remove_font_kwarg_from_tex ───────────────────────────────────────────────

class TestRemoveFontFromTex:

    def test_removes_from_tex(self):
        code = 'Tex(r"x^2", font="Comic Sans")'
        fixed, label = _remove_font_kwarg_from_tex(code)
        assert "font=" not in fixed
        assert label is not None

    def test_removes_from_textext(self):
        code = 'TexText("hello world", font="Arial")'
        fixed, label = _remove_font_kwarg_from_tex(code)
        assert "font=" not in fixed

    def test_does_not_touch_text(self):
        code = 'Text("hello", font="Arial")'
        fixed, label = _remove_font_kwarg_from_tex(code)
        assert "font=" in fixed
        assert label is None


# ── SurroundingRectangle/BackgroundRectangle AutoWrap ──────────────────────

class TestSurroundingRectangleAutoWrap:

    def test_bare_surrounding_rect_wrapped_in_show_creation(self):
        code = "self.play(SurroundingRectangle(obj))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(SurroundingRectangle(obj))" in fixed
        assert any("ShowCreation" in a for a in applied)

    def test_bare_surrounding_rect_with_kwargs_wrapped(self):
        code = "self.play(SurroundingRectangle(obj, color=YELLOW))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(SurroundingRectangle(obj, color=YELLOW))" in fixed

    def test_already_wrapped_not_double_wrapped(self):
        code = "self.play(ShowCreation(SurroundingRectangle(obj)))"
        fixed, applied = apply_known_fixes(code)
        assert fixed == code
        assert applied == []

    def test_bare_background_rectangle_wrapped(self):
        code = "self.play(BackgroundRectangle(obj))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(BackgroundRectangle(obj))" in fixed

    def test_surrounding_rect_with_run_time_kwarg_fixed(self):
        code = "self.play(SurroundingRectangle(obj, color=YELLOW), run_time=1.5)"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(SurroundingRectangle(obj, color=YELLOW))" in fixed
        assert "run_time=1.5" in fixed
        assert any("ShowCreation" in a for a in applied)

    def test_nested_mobject_arg_also_fixed(self):
        # SurroundingRectangle(VGroup(a, b)) with nested parens IS auto-fixed correctly
        # The regex [^)]* stops at the first ), but then group 3 captures that ), so
        # the replacement correctly re-emits it, balancing parens.
        code = "self.play(SurroundingRectangle(VGroup(a, b)))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(SurroundingRectangle(VGroup(a, b)))" in fixed
        assert fixed.count("(") == fixed.count(")")  # parens balanced
        assert any("ShowCreation" in a for a in applied)

    def test_validate_detects_bare_surrounding_rect(self):
        errors = validate_scene_code("self.play(SurroundingRectangle(obj))")
        assert any("ShowCreation" in e for e in errors)

    def test_multiple_nested_call_args_not_auto_fixed(self):
        # Known limitation: [^)]* stops at first ')' so multiple nested calls
        # as positional args are not safely auto-fixed. They ARE caught by the
        # banned pattern so validate_scene_code still fires for LLM retry.
        code = "self.play(SurroundingRectangle(func(a), func(b)))"
        fixed, applied = apply_known_fixes(code)
        # Do not assert on fixed content — behavior is undefined for this case.
        # Only assert the banned pattern still catches it:
        errors = validate_scene_code(code)
        assert any("ShowCreation" in e for e in errors)


# ── Tex() \text{} outer wrapper strip ──────────────────────────────────────

class TestTexTextOuterWrapperStrip:

    def test_strips_text_wrapper_single_quotes(self):
        code = r"label = Tex(r'\text{Bubble Sort}')"
        fixed, applied = apply_known_fixes(code)
        assert r"\text{" not in fixed
        assert "Tex(r'Bubble Sort')" in fixed
        assert any(r"\text{}" in a for a in applied)

    def test_strips_text_wrapper_double_quotes(self):
        code = r'label = Tex(r"\text{Step 1}")'
        fixed, applied = apply_known_fixes(code)
        assert r"\text{" not in fixed
        assert r'Tex(r"Step 1")' in fixed

    def test_does_not_strip_mid_expression(self):
        # \text{} used correctly inside an expression — must NOT be touched
        code = r'label = Tex(r"f(x) = \text{identity}")'
        fixed, applied = apply_known_fixes(code)
        assert r"\text{identity}" in fixed
        assert applied == []

    def test_does_not_strip_mixed_math_and_text(self):
        # Valid use: math with a text annotation
        code = r'label = Tex(r"\forall n \in \mathbb{N}, \text{n is positive}")'
        fixed, applied = apply_known_fixes(code)
        assert r"\text{n is positive}" in fixed
        assert applied == []

    def test_validate_detects_outer_text_wrapper(self):
        errors = validate_scene_code(r'Tex(r"\text{Bubble Sort}")')
        assert any(r"\text{}" in e for e in errors)
