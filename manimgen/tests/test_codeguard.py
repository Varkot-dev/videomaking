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

    def test_no_r_prefix_stripped(self):
        # Tex("\text{label}") without r prefix — should also strip
        code = 'label = Tex("\\text{Bubble Sort}")'
        fixed, applied = apply_known_fixes(code)
        assert "\\text{" not in fixed
        assert any("\\text{}" in a for a in applied)

    def test_multi_arg_second_text_flagged_by_validator(self):
        # Multi-arg: fix handles first arg only, but validator catches the second
        code = r'Tex(r"\text{a}", r"\text{b}")'
        errors = validate_scene_code(code)
        assert any(r"\text{}" in e for e in errors)


# ── VGroup item assignment ban ────────────────────────────────────────────

class TestVGroupItemAssignmentBan:

    def test_detects_double_index_assignment(self):
        errors = validate_scene_code("vgroup[i][j] = new_obj")
        assert any("VGroup" in e and "item assignment" in e for e in errors)

    def test_detects_single_index_assignment(self):
        errors = validate_scene_code("cells[0] = Text('new')")
        assert any("VGroup" in e and "item assignment" in e for e in errors)

    def test_normal_index_read_not_flagged(self):
        # Reading from index is fine
        errors = validate_scene_code("obj = vgroup[0]")
        assert not any("item assignment" in e for e in errors)

    def test_equality_comparison_not_flagged(self):
        # vgroup[0] == something — comparison, not assignment
        errors = validate_scene_code("if vgroup[0] == other: pass")
        assert not any("item assignment" in e for e in errors)

    def test_apply_known_fixes_makes_no_change(self):
        # No autofix — structural problem
        code = "vgroup[0][1] = new_mob"
        fixed, applied = apply_known_fixes(code)
        assert fixed == code
        assert applied == []


# ── font_size= on Tex() — not double-scaled ───────────────────────────────────

class TestFontSizeOnTexNotDoubleScaled:

    def test_font_size_on_tex_left_alone_by_error_aware(self):
        # font_size= is a valid Tex() kwarg — must NOT be converted to .scale()
        code = 'label = Tex(r"x^2 + y^2", font_size=48)'
        fixed, applied = apply_error_aware_fixes(
            code,
            "TypeError: Animation.__init__() got an unexpected keyword argument 'font_size'"
        )
        assert ".scale(" not in fixed
        assert "font_size=48" in fixed

    def test_font_size_not_stripped_by_known_fixes(self):
        # apply_known_fixes must not touch font_size= on Tex either
        code = 'eq = Tex(r"\\frac{1}{2}", font_size=36)'
        fixed, applied = apply_known_fixes(code)
        assert "font_size=36" in fixed
        assert applied == []

    def test_other_unexpected_kwarg_still_stripped(self):
        # Other genuinely unknown kwargs must still be stripped
        code = "FadeIn(obj, bogus_param=True)"
        stderr = "TypeError: Animation.__init__() got an unexpected keyword argument 'bogus_param'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "bogus_param" not in fixed
        assert any("bogus_param" in a for a in applied)


# ── set_camera_orientation → self.frame.reorient ──────────────────────────────

class TestFixSetCameraOrientation:

    def test_basic_phi_theta_with_degrees(self):
        code = "self.set_camera_orientation(phi=60 * DEGREES, theta=-45 * DEGREES)"
        fixed, applied = apply_known_fixes(code)
        assert "self.set_camera_orientation(" not in fixed
        assert "self.frame.reorient(-45, 60)" in fixed
        assert any("set_camera_orientation" in a for a in applied)

    def test_theta_before_phi(self):
        code = "self.set_camera_orientation(theta=-30 * DEGREES, phi=70 * DEGREES)"
        fixed, applied = apply_known_fixes(code)
        assert "self.frame.reorient(-30, 70)" in fixed

    def test_no_degrees_suffix(self):
        code = "self.set_camera_orientation(phi=60, theta=-45)"
        fixed, applied = apply_known_fixes(code)
        assert "self.frame.reorient(-45, 60)" in fixed

    def test_unparseable_becomes_pass(self):
        code = "self.set_camera_orientation(some_other_args)"
        fixed, applied = apply_known_fixes(code)
        assert "self.set_camera_orientation(" not in fixed
        assert "pass" in fixed

    def test_banned_pattern_detected(self):
        errors = validate_scene_code("self.set_camera_orientation(phi=60*DEGREES, theta=-45*DEGREES)")
        assert any("set_camera_orientation" in e for e in errors)


class TestFixReorientWrongKwargs:

    def test_theta_deg_renamed(self):
        code = "self.frame.reorient(theta_deg=-45, phi_deg=60)"
        fixed, applied = apply_known_fixes(code)
        assert "theta_degrees=-45" in fixed
        assert "phi_degrees=60" in fixed
        assert "theta_deg=" not in fixed
        assert "phi_deg=" not in fixed
        assert any("reorient kwarg" in a for a in applied)

    def test_animate_reorient_also_fixed(self):
        code = "self.play(self.frame.animate.reorient(theta_deg=0, phi_deg=90), run_time=1.5)"
        fixed, applied = apply_known_fixes(code)
        assert "theta_degrees=0" in fixed
        assert "phi_degrees=90" in fixed


class TestStripLabelFromNumberLine:

    def test_label_kwarg_stripped(self):
        code = 'ax = NumberLine(x_range=[-3, 3], label="x")'
        fixed, applied = apply_known_fixes(code)
        assert 'label=' not in fixed
        assert any("NumberLine" in a for a in applied)

    def test_valid_args_preserved(self):
        code = 'ax = NumberLine(x_range=[-3, 3, 1], include_tip=True, label="x")'
        fixed, applied = apply_known_fixes(code)
        assert "x_range" in fixed
        assert "include_tip" in fixed
        assert "label=" not in fixed


# ── Horizontal chain overflow detection ────────────────────────────────────

class TestHorizontalChainOverflow:

    def test_three_right_chain_detected(self):
        """3+ objects chained with .next_to(prev, RIGHT) should warn."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        code = (
            'step1 = Tex(r"a").next_to(rule, DOWN)\n'
            'step2 = Tex(r"b").next_to(step1, RIGHT, buff=0.2)\n'
            'step3 = Tex(r"c").next_to(step2, RIGHT, buff=0.2)\n'
            'self.play(FadeOut(step3))\n'
        )
        warnings = _check_layout_smells(code)
        assert any("Horizontal chain" in w for w in warnings), (
            f"Expected horizontal chain warning, got: {warnings}"
        )

    def test_two_right_not_flagged(self):
        """2 .next_to(RIGHT) is borderline but not flagged (chain length < 3)."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        code = (
            'step1 = Tex(r"a").next_to(rule, DOWN)\n'
            'step2 = Tex(r"b").next_to(step1, RIGHT, buff=0.2)\n'
            'self.play(FadeOut(step2))\n'
        )
        warnings = _check_layout_smells(code)
        assert not any("Horizontal chain" in w for w in warnings)

    def test_vertical_chain_not_flagged(self):
        """All .next_to(prev, DOWN) should not trigger horizontal warning."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        code = (
            'step1 = Tex(r"a").next_to(rule, DOWN)\n'
            'step2 = Tex(r"b").next_to(step1, DOWN)\n'
            'step3 = Tex(r"c").next_to(step2, DOWN)\n'
            'self.play(FadeOut(step3))\n'
        )
        warnings = _check_layout_smells(code)
        assert not any("Horizontal chain" in w for w in warnings)

    def test_next_to_parabola_right_detected(self):
        """Placing content RIGHT of parabola/axes should warn."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        code = (
            'table = VGroup().next_to(parabola, RIGHT, buff=1.0)\n'
            'self.play(FadeOut(table))\n'
        )
        warnings = _check_layout_smells(code)
        assert any("overflow" in w.lower() or "right screen edge" in w.lower() for w in warnings), (
            f"Expected right-of-axes overflow warning, got: {warnings}"
        )

    def test_next_to_axes_right_detected(self):
        """Placing content RIGHT of axes should warn."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        code = (
            'label = Text("info").next_to(axes, RIGHT, buff=0.5)\n'
            'self.play(FadeOut(label))\n'
        )
        warnings = _check_layout_smells(code)
        assert any("overflow" in w.lower() or "right screen edge" in w.lower() for w in warnings)

    def test_next_to_axes_down_not_flagged(self):
        """Placing content below axes is fine."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        code = (
            'label = Text("info").next_to(axes, DOWN, buff=0.5)\n'
            'self.play(FadeOut(label))\n'
        )
        warnings = _check_layout_smells(code)
        assert not any("right screen edge" in w.lower() for w in warnings)

    def test_section_05_triggers_warning(self):
        """The actual section_05.py from the failed render should trigger warnings."""
        from manimgen.validator.codeguard import _check_layout_smells, run_invariant_warnings
        # Simplified extract from the real section_05.py
        code = (
            'table_headers = VGroup().next_to(parabola, RIGHT, buff=1.0)\n'
            'self.play(FadeOut(table_headers))\n'
        )
        warnings = _check_layout_smells(code)
        assert any("right screen edge" in w.lower() or "overflow" in w.lower() for w in warnings)


# ── I3 — hex → palette constant auto-fix ────────────────────────────────────

class TestHexToPaletteConstantFix:

    def test_primary_hex_replaced(self):
        code = 'curve = axes.get_graph(lambda x: x, color="#00D9FF")'
        fixed, applied = apply_known_fixes(code)
        assert '"#00D9FF"' not in fixed
        assert "TEAL_A" in fixed
        assert any("TEAL_A" in a for a in applied)

    def test_secondary_hex_replaced(self):
        code = 'dot = Dot(color="#FF6B35")'
        fixed, applied = apply_known_fixes(code)
        assert '"#FF6B35"' not in fixed
        assert "GOLD" in fixed

    def test_success_hex_replaced(self):
        code = 'label = Text("OK", color="#3DD17B")'
        fixed, applied = apply_known_fixes(code)
        assert '"#3DD17B"' not in fixed
        assert "GREEN" in fixed

    def test_struct_hex_replaced(self):
        code = 'axes = Axes(axis_config={"color": "#3A6F8A"})'
        fixed, applied = apply_known_fixes(code)
        assert '"#3A6F8A"' not in fixed
        assert "GREY_B" in fixed

    def test_dark_bg_hex_replaced(self):
        code = 'rect = Rectangle(fill_color="#1C1C1C")'
        fixed, applied = apply_known_fixes(code)
        assert '"#1C1C1C"' not in fixed
        assert "GREY_E" in fixed

    def test_unknown_hex_left_alone(self):
        code = 'dot = Dot(color="#ABCDEF")'
        fixed, applied = apply_known_fixes(code)
        assert '"#ABCDEF"' in fixed
        assert not any("ABCDEF" in a for a in applied)

    def test_multiple_hex_in_one_file(self):
        code = (
            'curve = axes.get_graph(lambda x: x, color="#00D9FF")\n'
            'area = axes.get_area(curve, color="#FF6B35")\n'
        )
        fixed, applied = apply_known_fixes(code)
        assert '"#00D9FF"' not in fixed
        assert '"#FF6B35"' not in fixed
        assert "TEAL_A" in fixed
        assert "GOLD" in fixed


# ── I4 — font_size off canonical type scale warning ──────────────────────────

class TestFontSizeCanonicalScaleWarning:
    """Invariant I4 — warns on off-scale font_size literals.

    The auto-fix _fix_font_size_to_scale snaps off-scale values to the nearest
    canonical size, so in practice these warnings rarely fire at runtime. The
    invariant is tested directly to guarantee coverage if an off-scale literal
    ever reaches run_invariant_warnings without being auto-fixed first.
    """

    def test_valid_font_sizes_no_warning(self):
        from manimgen.validator.codeguard import run_invariant_warnings
        code = (
            'title = Text("Hello", font_size=48)\n'
            'sub = Text("world", font_size=28)\n'
            'caption = Text("note", font_size=18)\n'
        )
        warnings = run_invariant_warnings(code)
        assert not any(w.startswith("I4:") for w in warnings)

    def test_off_scale_font_size_warns(self):
        from manimgen.validator.codeguard import run_invariant_warnings
        code = 'label = Text("hi", font_size=32)'
        warnings = run_invariant_warnings(code)
        assert any("font_size=32" in w and "I4" in w for w in warnings)

    def test_all_valid_sizes_no_warning(self):
        from manimgen.validator.codeguard import run_invariant_warnings
        for size in [48, 44, 36, 28, 22, 20, 18]:
            code = f'label = Text("x", font_size={size})'
            warnings = run_invariant_warnings(code)
            assert not any(w.startswith("I4:") for w in warnings), \
                f"font_size={size} should not warn"

    def test_font_size_24_warns(self):
        from manimgen.validator.codeguard import run_invariant_warnings
        code = 'tick = Text("0", font_size=24)'
        warnings = run_invariant_warnings(code)
        assert any("font_size=24" in w for w in warnings)

    def test_font_size_in_dict_not_checked(self):
        # The checker uses the `font_size=` kwarg syntax — dict-style "font_size": N
        # is not caught (acceptable limitation; Director uses kwarg form).
        from manimgen.validator.codeguard import run_invariant_warnings
        code = 'decimal_number_config={"font_size": 16}'
        warnings = run_invariant_warnings(code)
        assert not any("font_size=16" in w for w in warnings)


# ── I5: FadeOut cleanup check ─────────────────────────────────────────────────

class TestI5FadeOutCleanup:

    def _smells(self, code):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def _i5_warnings(self, code):
        return [w for w in self._smells(code) if "I5" in w]

    def test_no_fadeout_at_all_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('hello')\n"
            "        self.play(FadeIn(t))\n"
            "        self.wait(1)\n"
        )
        assert self._i5_warnings(code), "Expected I5 warning when no FadeOut present"

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
        assert not self._i5_warnings(code), "Expected no I5 warning with full FadeOut cleanup"

    def test_fadeout_without_self_mobjects_warns(self):
        # FadeOut present but not the full cleanup pattern in the tail
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('hello')\n"
            "        self.play(FadeIn(t))\n"
            "        self.wait(1)\n"
            "        self.play(FadeOut(t))\n"
        )
        assert self._i5_warnings(code), "Expected I5 warning when FadeOut lacks self.mobjects"

    def test_full_fadeout_buried_deep_warns(self):
        # Full cleanup pattern exists but is more than 15 lines from the end
        body_lines = ["        self.wait(0.1)\n"] * 20
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
            + "".join(body_lines)
        )
        assert self._i5_warnings(code), (
            "Expected I5 warning when full FadeOut is not in the last 15 lines"
        )


# ── I7: ThreeDScene fix_in_frame check ───────────────────────────────────────

class TestI7ThreeDFixInFrame:

    def _smells(self, code):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def _i7_warnings(self, code):
        return [w for w in self._smells(code) if "I7" in w]

    def test_threedscene_text_missing_fix_in_frame_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        label = Text('hello')\n"
            "        self.play(FadeIn(label))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        w = self._i7_warnings(code)
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
        assert not self._i7_warnings(code), "Expected no I7 warning when fix_in_frame is present"

    def test_regular_scene_text_no_warn(self):
        # Only ThreeDScene triggers I7 — plain Scene should never warn
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        label = Text('hello')\n"
            "        self.play(FadeIn(label))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not self._i7_warnings(code), "Expected no I7 warning for plain Scene"

    def test_threedscene_tex_missing_fix_in_frame_warns(self):
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        eq = Tex(r'x^2')\n"
            "        self.play(Write(eq))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        w = self._i7_warnings(code)
        assert w, "Expected I7 warning for Tex without fix_in_frame"
        assert "eq" in w[0]

    def test_inline_text_not_warned(self):
        # Inline use like self.play(FadeIn(Text('x'))) — no variable assignment — no I7
        code = (
            "from manimlib import *\n"
            "class Foo(ThreeDScene):\n"
            "    def construct(self):\n"
            "        self.play(FadeIn(Text('hello')))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        assert not self._i7_warnings(code), (
            "Expected no I7 warning for inline Text (no variable assignment)"
        )


# ── I9 — high mobject density heuristic ─────────────────────────────────────

class TestI9DensityWarning:

    @staticmethod
    def _warnings(code: str):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def _make_code(self, n: int) -> str:
        """Generate a scene with n distinct mobject creations plus a FadeOut."""
        lines = []
        mobject_types = [
            "Text", "Tex", "Dot", "Circle", "Arrow",
            "Line", "Rectangle", "VGroup", "Axes", "NumberLine",
            "ParametricSurface",
        ]
        for i in range(n):
            t = mobject_types[i % len(mobject_types)]
            lines.append(f"    obj{i} = {t}()")
        lines.append("    self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)")
        return "\n".join(lines)

    def test_11_objects_warns(self):
        code = self._make_code(11)
        warnings = self._warnings(code)
        assert any("I9" in w for w in warnings), (
            f"Expected I9 density warning for 11 objects, got: {warnings}"
        )
        assert any("11" in w for w in warnings)

    def test_5_objects_no_warn(self):
        code = self._make_code(5)
        warnings = self._warnings(code)
        assert not any("I9" in w for w in warnings), (
            f"Expected no I9 warning for 5 objects, got: {warnings}"
        )

    def test_exactly_10_objects_no_warn(self):
        """Boundary: exactly 10 creations should NOT warn (threshold is > 10)."""
        code = self._make_code(10)
        warnings = self._warnings(code)
        assert not any("I9" in w for w in warnings), (
            f"Expected no I9 warning at threshold boundary (10 objects), got: {warnings}"
        )

    def test_warning_mentions_vgroup_suggestion(self):
        code = self._make_code(12)
        warnings = self._warnings(code)
        assert any("VGroup" in w for w in warnings)


# ── I2 — zone grammar: .to_edge(UP) enforcement ──────────────────────────────

class TestI2ZoneGrammar:

    @staticmethod
    def _warnings(code: str):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def test_single_to_edge_up_no_warn(self):
        """One .to_edge(UP) is allowed — the section title."""
        code = (
            "title = Text('Section').to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        warnings = self._warnings(code)
        assert not any("I2" in w and "Multiple" in w for w in warnings), (
            f"Single to_edge(UP) should not warn, got: {warnings}"
        )

    def test_double_to_edge_up_warns(self):
        """Two .to_edge(UP) calls should trigger the I2 multiple-calls warning."""
        code = (
            "title = Text('Section').to_edge(UP)\n"
            "subtitle = Text('Sub').to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        warnings = self._warnings(code)
        assert any("I2" in w and "Multiple" in w for w in warnings), (
            f"Expected I2 multiple to_edge(UP) warning, got: {warnings}"
        )

    def test_axes_to_edge_up_warns(self):
        """axes.to_edge(UP) pushes content into TITLE zone — should warn."""
        code = (
            "axes = Axes(x_range=[-3, 3], y_range=[-2, 2])\n"
            "axes.to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        warnings = self._warnings(code)
        assert any("I2" in w and "axes" in w.lower() for w in warnings), (
            f"Expected I2 content-in-title-zone warning for axes.to_edge(UP), got: {warnings}"
        )

    def test_curve_to_edge_up_warns(self):
        """curve.to_edge(UP) pushes content into TITLE zone — should warn."""
        code = (
            "curve = axes.get_graph(lambda x: x**2)\n"
            "curve.to_edge(UP)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        warnings = self._warnings(code)
        assert any("I2" in w for w in warnings), (
            f"Expected I2 warning for curve.to_edge(UP), got: {warnings}"
        )

    def test_to_edge_down_not_flagged(self):
        """to_edge(DOWN) is fine and should not trigger I2."""
        code = (
            "footer = Text('note').to_edge(DOWN)\n"
            "footer2 = Text('note2').to_edge(DOWN)\n"
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        warnings = self._warnings(code)
        assert not any("I2" in w for w in warnings), (
            f"to_edge(DOWN) should not trigger I2, got: {warnings}"
        )


# ── Fix 1: rename-aware kwarg fixer ──────────────────────────────────────────

class TestRenameAwareKwargFixer:

    def test_renames_kwarg_when_hint_present(self):
        """Registry path: when a known method fires, ALL its wrong kwargs are fixed at once."""
        code = "grid.arrange_in_grid(rows=20, cols=50, row_buff=0.01, col_buff=0.01)"
        stderr = (
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'rows'. "
            "Did you mean 'n_rows'?"
        )
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "n_rows=20" in fixed, f"Expected n_rows=20 in: {fixed}"
        # Registry fixes ALL kwargs in one pass — cols becomes n_cols too
        assert "n_cols=50" in fixed, f"Expected n_cols=50 (registry), got: {fixed}"
        import re as _re
        assert not _re.search(r"(?<!n_)rows=", fixed), f"Old kwarg 'rows=' should be gone: {fixed}"
        assert any("rows" in a for a in applied), f"Applied: {applied}"

    def test_strips_kwarg_when_no_hint(self):
        """Without 'Did you mean', fall back to stripping the bad kwarg."""
        code = "FadeIn(obj, run_time=1, bogus_param=True)"
        stderr = "TypeError: Animation.__init__() got an unexpected keyword argument 'bogus_param'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "bogus_param" not in fixed
        assert any("bogus_param" in a for a in applied)

    def test_font_size_never_touched_even_with_hint(self):
        """font_size= is always left alone, regardless of any hint in stderr."""
        code = "Tex(r'x^2', font_size=48)"
        stderr = (
            "TypeError: Tex.__init__() got an unexpected keyword argument 'font_size'. "
            "Did you mean 'scale'?"
        )
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "font_size=48" in fixed, f"font_size must not be renamed, got: {fixed}"
        assert not any("font_size" in a for a in applied), f"Applied should not touch font_size: {applied}"


# ── Fix 2: mobject explosion gate ────────────────────────────────────────────

class TestMobjectExplosionGate:

    def test_vgroup_range_1000_rejected(self):
        """VGroup with range(1000) is infeasible — validate_scene_code must return an error."""
        code = "boxes = VGroup(*[Square() for _ in range(1000)])"
        errors = validate_scene_code(code)
        assert any("1000" in e and "infeasible" in e for e in errors), (
            f"Expected infeasible error mentioning 1000, got: {errors}"
        )

    def test_vgroup_range_1000_message_has_symbolic_guidance(self):
        """Error message must include the N value (×) so the retry LLM gets actionable guidance."""
        code = "boxes = VGroup(*[Square() for _ in range(1000)])"
        errors = validate_scene_code(code)
        assert any("1000" in e for e in errors), f"N value missing from error: {errors}"

    def test_vgroup_range_50_allowed(self):
        """N=50 is at the threshold — must not be rejected."""
        code = "boxes = VGroup(*[Square() for _ in range(50)])"
        errors = validate_scene_code(code)
        explosion_errors = [e for e in errors if "infeasible" in e]
        assert not explosion_errors, f"N=50 should be allowed, got: {explosion_errors}"

    def test_vgroup_range_8_allowed(self):
        """N=8 is a common legit case — must not be rejected."""
        code = "boxes = VGroup(*[Square() for _ in range(8)])"
        errors = validate_scene_code(code)
        explosion_errors = [e for e in errors if "infeasible" in e]
        assert not explosion_errors, f"N=8 should be allowed, got: {explosion_errors}"


# ── Fix 4a: arrange_in_grid kwarg normalization ───────────────────────────────

class TestFixArrangeInGridKwargs:
    """4a — proactive one-pass normalization of all wrong arrange_in_grid kwargs."""

    def test_rows_renamed_to_n_rows(self):
        code = "grid.arrange_in_grid(rows=4, n_cols=3)"
        fixed, applied = apply_known_fixes(code)
        assert "n_rows=4" in fixed, f"Expected n_rows=4, got: {fixed}"
        assert "rows=4" not in fixed or "n_rows=4" in fixed

    def test_cols_renamed_to_n_cols(self):
        code = "grid.arrange_in_grid(n_rows=4, cols=3)"
        fixed, applied = apply_known_fixes(code)
        assert "n_cols=3" in fixed, f"Expected n_cols=3, got: {fixed}"

    def test_row_buff_renamed_to_buff(self):
        code = "grid.arrange_in_grid(n_rows=4, n_cols=3, row_buff=0.2)"
        fixed, applied = apply_known_fixes(code)
        assert "row_buff" not in fixed, f"row_buff should be gone: {fixed}"
        assert "buff=0.2" in fixed, f"Expected buff=0.2, got: {fixed}"

    def test_col_buff_renamed_to_buff(self):
        code = "grid.arrange_in_grid(n_rows=4, n_cols=3, col_buff=0.3)"
        fixed, applied = apply_known_fixes(code)
        assert "col_buff" not in fixed, f"col_buff should be gone: {fixed}"
        assert "buff=0.3" in fixed, f"Expected buff=0.3, got: {fixed}"

    def test_all_four_wrong_kwargs_fixed_in_one_pass(self):
        """The core regression test — all four wrong kwargs fixed before first render."""
        code = "grid.arrange_in_grid(rows=20, cols=50, row_buff=0.01, col_buff=0.01)"
        fixed, applied = apply_known_fixes(code)
        assert "n_rows=20" in fixed, f"Expected n_rows=20 in: {fixed}"
        assert "n_cols=50" in fixed, f"Expected n_cols=50 in: {fixed}"
        assert "row_buff" not in fixed, f"row_buff should be gone: {fixed}"
        assert "col_buff" not in fixed, f"col_buff should be gone: {fixed}"
        assert any("arrange_in_grid" in a for a in applied), f"Applied: {applied}"

    def test_correct_kwargs_not_touched(self):
        """n_rows= and n_cols= already correct — must not be double-renamed."""
        code = "grid.arrange_in_grid(n_rows=4, n_cols=3, buff=0.2)"
        fixed, applied = apply_known_fixes(code)
        assert "n_rows=4" in fixed
        assert "n_cols=3" in fixed
        assert "buff=0.2" in fixed

    def test_non_arrange_in_grid_rows_not_touched(self):
        """rows= on a different method must not be renamed."""
        code = "some_other_func(rows=4)"
        fixed, applied = apply_known_fixes(code)
        assert "rows=4" in fixed, f"rows= on non-arrange_in_grid must be left alone: {fixed}"


# ── Fix 4b: kwarg normalization registry ─────────────────────────────────────

class TestKwargNormalizationRegistry:
    """4b — apply_error_aware_fixes uses registry to fix ALL known kwargs for a method."""

    def test_registry_fixes_all_arrange_in_grid_kwargs_on_rows_error(self):
        """When rows= error fires, ALL wrong arrange_in_grid kwargs fixed in one call."""
        code = "grid.arrange_in_grid(rows=20, cols=50, row_buff=0.01, col_buff=0.01)"
        stderr = (
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'rows'. "
            "Did you mean 'n_rows'?"
        )
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "n_rows=20" in fixed, f"n_rows missing: {fixed}"
        assert "n_cols=50" in fixed, f"n_cols missing: {fixed}"
        assert "row_buff" not in fixed, f"row_buff should be gone: {fixed}"
        assert "col_buff" not in fixed, f"col_buff should be gone: {fixed}"

    def test_registry_fixes_all_reorient_kwargs_on_theta_deg_error(self):
        """When theta_deg= error fires, both reorient kwargs fixed."""
        code = "self.frame.reorient(theta_deg=-45, phi_deg=60)"
        stderr = (
            "TypeError: CameraFrame.reorient() got an unexpected keyword argument 'theta_deg'. "
            "Did you mean 'theta_degrees'?"
        )
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "theta_degrees=-45" in fixed, f"theta_degrees missing: {fixed}"
        assert "phi_degrees=60" in fixed, f"phi_degrees missing: {fixed}"
        assert "theta_deg=" not in fixed
        assert "phi_deg=" not in fixed

    def test_unknown_method_falls_back_to_single_rename(self):
        """Method not in registry → existing single-rename behavior preserved."""
        code = "FadeIn(obj, run_time=1, bogus_param=True)"
        stderr = "TypeError: Animation.__init__() got an unexpected keyword argument 'bogus_param'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "bogus_param" not in fixed
        assert any("bogus_param" in a for a in applied)


# ── Fix 4c: root-cause error signature ───────────────────────────────────────

class TestRootCauseErrorSignature:
    """4c — error signature captures method+error_class, not raw kwarg name."""

    def _sig(self, error_type, stderr):
        from manimgen.validator.retry import _build_error_signature
        return _build_error_signature(error_type, stderr)

    def test_arrange_in_grid_rows_and_cols_produce_same_signature(self):
        """rows= and cols= on arrange_in_grid have the same root cause."""
        sig1 = self._sig("type",
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'rows'.")
        sig2 = self._sig("type",
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'cols'.")
        assert sig1 == sig2, (
            f"rows= and cols= should share signature (same root cause).\n"
            f"sig1={sig1}\nsig2={sig2}"
        )

    def test_different_methods_produce_different_signatures(self):
        """arrange_in_grid and reorient are different root causes."""
        sig1 = self._sig("type",
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'rows'.")
        sig2 = self._sig("type",
            "TypeError: CameraFrame.reorient() got an unexpected keyword argument 'theta_deg'.")
        assert sig1 != sig2, "Different methods must have different signatures"

    def test_non_kwarg_typeerror_falls_back_to_truncated_stderr(self):
        """TypeErrors not about unexpected kwargs fall back to the original behavior."""
        sig = self._sig("type", "TypeError: unsupported operand type(s) for +: 'int' and 'str'")
        assert sig.startswith("type:"), f"Expected type: prefix, got: {sig}"
        assert "unexpected_kwarg" not in sig


# ── Fix 4d: targeted TYPE error guidance ─────────────────────────────────────

class TestTargetedTypeErrorGuidance:
    """4d — _fix_guidance extracts bad kwarg + hint from stderr for TYPE errors."""

    def _guidance(self, error_type, stderr=""):
        from manimgen.validator.retry import _fix_guidance, SceneErrorType
        return _fix_guidance(error_type, stderr)

    def test_type_error_with_kwarg_and_hint_mentions_both(self):
        stderr = (
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'rows'. "
            "Did you mean 'n_rows'?"
        )
        guidance = self._guidance("type", stderr)
        assert "rows" in guidance, f"Bad kwarg missing from guidance: {guidance}"
        assert "n_rows" in guidance, f"Hint kwarg missing from guidance: {guidance}"
        assert "arrange_in_grid" in guidance, f"Method name missing: {guidance}"

    def test_type_error_with_kwarg_no_hint_still_names_kwarg(self):
        stderr = (
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'row_buff'"
        )
        guidance = self._guidance("type", stderr)
        assert "row_buff" in guidance, f"Bad kwarg missing from guidance: {guidance}"

    def test_type_error_no_kwarg_returns_generic(self):
        """Non-kwarg TypeError keeps the generic guidance string."""
        stderr = "TypeError: unsupported operand type(s) for +: 'int' and 'str'"
        guidance = self._guidance("type", stderr)
        assert "type error" in guidance.lower() or "argument" in guidance.lower()

    def test_non_type_error_unaffected(self):
        """SYNTAX guidance is unchanged regardless of stderr."""
        g1 = self._guidance("syntax", "")
        g2 = self._guidance("syntax", "TypeError: something")
        assert g1 == g2, "Non-TYPE errors must not be affected by stderr"

    def test_guidance_instructs_fix_all_kwargs(self):
        """Guidance must tell LLM to fix ALL kwargs, not just the named one."""
        stderr = (
            "TypeError: VGroup.arrange_in_grid() got an unexpected keyword argument 'rows'. "
            "Did you mean 'n_rows'?"
        )
        guidance = self._guidance("type", stderr)
        assert "all" in guidance.lower() or "ALL" in guidance, (
            f"Guidance must instruct fixing ALL kwargs: {guidance}"
        )


# ── Fix 5c: retry system prompt caching ──────────────────────────────────────

class TestRetrySystemPromptCache:
    """5c — _load_retry_system_prompt() reads files only once across multiple calls."""

    def test_returns_nonempty_string(self):
        from manimgen.validator.retry import _load_retry_system_prompt
        result = _load_retry_system_prompt()
        assert isinstance(result, str) and len(result) > 100

    def test_second_call_returns_identical_result(self):
        from manimgen.validator.retry import _load_retry_system_prompt
        result1 = _load_retry_system_prompt()
        result2 = _load_retry_system_prompt()
        assert result1 == result2

    def test_cached_after_first_call(self, monkeypatch):
        """After first call, open() must NOT be called again."""
        from manimgen.validator import retry as retry_mod
        # Prime the cache with a real call first
        retry_mod._load_retry_system_prompt()

        open_calls = []
        real_open = open

        def tracking_open(path, *args, **kwargs):
            open_calls.append(path)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", tracking_open)
        retry_mod._load_retry_system_prompt()
        prompt_file_opens = [p for p in open_calls if "retry_system" in str(p) or "director_system" in str(p)]
        assert not prompt_file_opens, (
            f"Prompt files opened on second call — cache not working: {prompt_file_opens}"
        )


# ── Invariant registry — architectural contract ──────────────────────────────

class TestInvariantRegistry:
    """The registry is the canonical list of design-system rules.

    These tests pin the contract: run_all returns (errors, warnings), ERROR
    severity blocks the render, WARNING severity surfaces guidance. Adding a
    new invariant is one row in the registry — these tests validate the row
    plumbing, not every check's content.
    """

    def test_run_all_returns_errors_and_warnings(self):
        from manimgen.validator.invariants import run_all
        code = "boxes = VGroup(*[Square() for _ in range(1000)])\n" \
               'label = Text("hi", font_size=26)\n'
        errors, warnings = run_all(code)
        assert errors, "Expected mobject explosion error"
        assert any("1000" in e for e in errors)
        # Off-scale font_size surfaces as a warning (auto-fix handles it too)
        assert any("I4" in w for w in warnings)

    def test_clean_code_has_no_errors(self):
        from manimgen.validator.invariants import run_all
        code = (
            "from manimlib import *\n"
            "class Foo(Scene):\n"
            "    def construct(self):\n"
            "        title = Text('Hi', font_size=48).to_edge(UP, buff=0.8)\n"
            "        self.play(Write(title))\n"
            "        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)\n"
        )
        errors, _ = run_all(code)
        assert errors == [], f"Clean code produced errors: {errors}"

    def test_validate_scene_code_flows_errors_from_registry(self):
        code = "boxes = VGroup(*[Square() for _ in range(500)])"
        errors = validate_scene_code(code)
        assert any("500" in e and "infeasible" in e for e in errors)

    def test_severity_enum_is_a_closed_set(self):
        from manimgen.validator.invariants import Severity, INVARIANTS
        for inv in INVARIANTS:
            assert isinstance(inv.severity, Severity), (
                f"Invariant {inv.id} has non-Severity severity {inv.severity}"
            )


class TestInvariantI3RoleConstants:
    """I3 role-constant warnings — flags raw RED/GREEN/YELLOW in color kwargs."""

    @staticmethod
    def _warnings(code: str):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def test_color_red_warns(self):
        code = "Square(color=RED)"
        warnings = self._warnings(code)
        assert any("RED" in w and "ALERT" in w for w in warnings)

    def test_set_fill_green_warns(self):
        code = "box.set_fill(GREEN, opacity=0.5)"
        warnings = self._warnings(code)
        assert any("GREEN" in w and "SUCCESS" in w for w in warnings)

    def test_stroke_yellow_warns(self):
        code = "box.set_stroke(YELLOW, width=3)"
        warnings = self._warnings(code)
        assert any("YELLOW" in w and "WARNING" in w for w in warnings)

    def test_teal_not_warned(self):
        code = "Square(color=TEAL_A)"
        warnings = self._warnings(code)
        assert not any(w.startswith("I3: raw TEAL") for w in warnings)

    def test_word_boundary_avoids_false_positives(self):
        code = "reduced = 3\ngreenhouse = True"
        warnings = self._warnings(code)
        assert not any("RED" in w and "ALERT" in w for w in warnings)
        assert not any("GREEN" in w and "SUCCESS" in w for w in warnings)


class TestInvariantI3RawHex:
    """I3 raw hex — sanctions the two design-system hexes, warns on everything else."""

    @staticmethod
    def _warnings(code: str):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def test_unsanctioned_hex_warns(self):
        code = 'Square(fill_color="#FF00FF")'
        warnings = self._warnings(code)
        assert any("#FF00FF" in w for w in warnings)

    def test_sanctioned_struct_hex_not_flagged(self):
        # "#2a2a2a" is the design-system STRUCT fill — not an I3 violation.
        code = 'Square(fill_color="#2a2a2a")'
        warnings = self._warnings(code)
        assert not any("#2a2a2a" in w for w in warnings)

    def test_sanctioned_background_hex_not_flagged(self):
        code = 'self.camera.background_color = "#1C1C1C"'
        warnings = self._warnings(code)
        assert not any("#1C1C1C" in w for w in warnings)


class TestInvariantI2CornerTitle:
    """I2 — titles must never be placed with .to_corner()."""

    @staticmethod
    def _warnings(code: str):
        from manimgen.validator.codeguard import run_invariant_warnings
        return run_invariant_warnings(code)

    def test_title_to_corner_ul_warns(self):
        code = "title_left = Text('A').to_corner(UL, buff=0.3)"
        warnings = self._warnings(code)
        assert any("title" in w.lower() and "to_corner" in w for w in warnings)

    def test_title_to_corner_ur_warns(self):
        code = "section_title = Text('B').to_corner(UR)"
        warnings = self._warnings(code)
        assert any("title" in w.lower() and "to_corner" in w for w in warnings)

    def test_non_title_to_corner_not_flagged(self):
        code = "counter = Text('3').to_corner(DR)"
        warnings = self._warnings(code)
        assert not any("title" in w.lower() and "to_corner" in w for w in warnings)


# ── Font-size auto-fix (token-free snap to canonical scale) ──────────────────

class TestFontSizeAutoFix:
    """apply_known_fixes snaps off-scale font_size literals to the nearest canonical value."""

    def test_snaps_26_to_28(self):
        code = 'Text("x", font_size=26)'
        fixed, applied = apply_known_fixes(code)
        assert "font_size=28" in fixed
        assert any("font_size=26 -> 28" in a for a in applied)

    def test_snaps_32_to_36(self):
        code = 'Text("x", font_size=32)'
        fixed, applied = apply_known_fixes(code)
        assert "font_size=36" in fixed

    def test_snaps_56_to_48(self):
        code = 'Text("x", font_size=56)'
        fixed, applied = apply_known_fixes(code)
        assert "font_size=48" in fixed

    def test_canonical_value_not_touched(self):
        code = 'Text("x", font_size=28)'
        fixed, applied = apply_known_fixes(code)
        assert "font_size=28" in fixed
        assert not any("font_size=28" in a and "->" in a for a in applied)

    def test_all_canonical_values_pass_through(self):
        for size in [48, 44, 36, 28, 22, 20, 18]:
            code = f'Text("x", font_size={size})'
            fixed, _ = apply_known_fixes(code)
            assert f"font_size={size}" in fixed, f"Canonical {size} was modified"
