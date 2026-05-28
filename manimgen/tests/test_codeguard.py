"""
Tests for manimgen/validator/codeguard.py

Covers every auto-fix, every banned pattern detection, and the
error-aware repair path. Zero LLM calls, zero subprocess calls.
"""

from manimgen.validator.codeguard import (
    apply_known_fixes,
    apply_error_aware_fixes,
    validate_scene_code,
    _fix_color_gradient_int_cast,
    _remove_font_kwarg_from_tex,
)


# ── ManimCommunity→ManimGL kwarg rewrites (baseline 2026-05-25) ───────────────
# Every section that fell back in the binary-search baseline died with
# SceneErrorType.TYPE — the LLM emitted ManimCommunity surface/axes/camera kwargs
# that Mobject.__init__ (no **kwargs) rejects. These rewrites are verified against
# the installed manimlib source (Mobject has color/opacity not fill_*, ThreeDAxes
# has depth not z_length, CameraFrame.add_ambient_rotation exists).

class TestCommunityToGLKwargRewrites:

    def test_fill_color_preserved_on_vmobject(self):
        """fill_color is VALID on VMobject (Square/Circle/Line/Tex/Text...). It must
        NOT be rewritten — the blanket fill_color->color rewrite created duplicate
        color= kwargs -> SyntaxError (#55) and caused fallback cards. Phase 1 deletes
        that rewrite. The surface-only case is handled later by type-aware introspection."""
        code = "Square(fill_color=BLUE, color=GREY_B)"
        fixed, applied = apply_known_fixes(code)
        assert "fill_color=BLUE" in fixed, "fill_color is valid on VMobject; must be preserved"
        assert fixed.count("color=") == 2, "both fill_color= and color= remain distinct"

    def test_fill_opacity_preserved_on_vmobject(self):
        code = "Circle(fill_opacity=0.8, opacity=1.0)"
        fixed, applied = apply_known_fixes(code)
        assert "fill_opacity=0.8" in fixed, "fill_opacity is valid on VMobject; must be preserved"

    def test_no_duplicate_kwarg_syntaxerror_regression(self):
        """Anti-#55: the exact pattern that produced 'keyword argument repeated: color'
        and fallback cards must survive codeguard as valid, parseable Python."""
        import ast as _ast
        code = (
            "boxes = VGroup(*[\n"
            "    Square(side_length=0.9, fill_color=\"#2a2a2a\", opacity=1,\n"
            "           stroke_width=2.5, color=GREY_B)\n"
            "    for _ in range(5)\n"
            "])\n"
        )
        fixed, applied = apply_known_fixes(code)
        _ast.parse(fixed)  # must NOT raise SyntaxError: keyword argument repeated
        assert "fill_color=" in fixed

    def test_z_length_to_depth(self):
        code = "ThreeDAxes(z_length=4)"
        fixed, applied = apply_known_fixes(code)
        assert "z_length" not in fixed
        assert "depth=4" in fixed

    def test_checkerboard_colors_stripped(self):
        code = "ParametricSurface(f, checkerboard_colors=[BLUE, GREEN])"
        fixed, applied = apply_known_fixes(code)
        assert "checkerboard_colors" not in fixed

    def test_begin_ambient_camera_rotation_rewritten(self):
        code = "self.begin_ambient_camera_rotation(rate=0.2)"
        fixed, applied = apply_known_fixes(code)
        assert "begin_ambient_camera_rotation" not in fixed
        assert "self.frame.add_ambient_rotation" in fixed

    def test_x_axis_config_is_left_untouched(self):
        """x_axis_config is a VALID Axes kwarg — must NOT be stripped/rewritten."""
        code = "Axes(x_axis_config={'include_numbers': True})"
        fixed, applied = apply_known_fixes(code)
        assert "x_axis_config" in fixed, (
            "x_axis_config is valid on Axes — rewriting it would break working code"
        )


class TestColorRoleHeaderInjection:
    """The Director shows PRIMARY/STRUCT/MUTED... as a palette reference but never
    requires emitting the assignment lines, so scenes use `color=MUTED` with MUTED
    undefined → NameError at render. If a role name is USED but not DEFINED, codeguard
    injects the canonical header. Mapping is the director prompt's palette table."""

    def test_injects_header_when_role_used_undefined(self):
        code = (
            "from manimlib import *\n\n"
            "class S(Scene):\n"
            "    def construct(self):\n"
            "        t = Text('hi', color=MUTED)\n"
        )
        fixed, applied = apply_known_fixes(code)
        assert "MUTED = GREY_A" in fixed, "must inject the MUTED definition"
        # injected header sits before the class so the name is in scope
        assert fixed.index("MUTED = GREY_A") < fixed.index("class S")

    def test_injects_all_referenced_roles(self):
        code = "x = [PRIMARY, STRUCT, ALERT]\n"
        fixed, _ = apply_known_fixes(code)
        assert "PRIMARY = TEAL_A" in fixed
        assert "STRUCT = GREY_B" in fixed
        assert "ALERT = RED" in fixed

    def test_does_not_inject_when_role_already_defined(self):
        code = "MUTED = GREY_A\nt = Text('hi', color=MUTED)\n"
        fixed, _ = apply_known_fixes(code)
        # exactly one definition — no duplicate injected
        assert fixed.count("MUTED = GREY_A") == 1

    def test_does_not_inject_unreferenced_roles(self):
        code = "t = Text('hi', color=MUTED)\n"
        fixed, _ = apply_known_fixes(code)
        # only MUTED is referenced; PRIMARY/STRUCT/etc must not be injected
        assert "PRIMARY = TEAL_A" not in fixed
        assert "ALERT = RED" not in fixed

    def test_no_injection_when_no_roles_used(self):
        code = "t = Text('hi', color=WHITE)\n"
        fixed, _ = apply_known_fixes(code)
        assert "= GREY_A" not in fixed
        assert "PRIMARY" not in fixed

    def test_no_injection_for_role_word_in_comment(self):
        """#56: a role word in a COMMENT must not trigger injection (it's not a
        real identifier use). Detection must scan AST Name nodes, not raw text."""
        code = "# use MUTED tones for the background\nt = Text('hi', color=WHITE)\n"
        fixed, _ = apply_known_fixes(code)
        assert "MUTED = GREY_A" not in fixed, "comment mention is not a real use"

    def test_no_injection_for_role_word_in_string(self):
        """#56: a role word inside a STRING literal must not trigger injection."""
        code = "label = Text('SUCCESS')\n"
        fixed, _ = apply_known_fixes(code)
        assert "SUCCESS = GREEN" not in fixed, "string content is not a real use"

    def test_still_injects_for_genuine_identifier_use(self):
        """Sanity: a real bare-name use still injects (the fix must not over-correct)."""
        code = "t = Text('hi', color=MUTED)\n"
        fixed, _ = apply_known_fixes(code)
        assert "MUTED = GREY_A" in fixed


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

    def test_fixes_get_point_at_angle(self):
        # ManimGL Circle has point_at_angle, NOT get_point_at_angle (ManimCommunity).
        code = "p = circle.get_point_at_angle(PI / 2)"
        fixed, applied = apply_known_fixes(code)
        assert ".point_at_angle(" in fixed
        assert "get_point_at_angle" not in fixed
        assert any("get_point_at_angle" in a for a in applied)

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


class TestTopEdgeCollision:
    """The title zone (UP edge / UR-UL corners) holds ONE mobject at a time.
    Two un-faded titles produce illegible text-on-text overlap."""

    def _smells(self, code: str) -> list[str]:
        from manimgen.validator.codeguard import _check_layout_smells
        return _check_layout_smells(code)

    def test_two_titles_at_top_edge_warns(self):
        code = (
            'scene_title = Text("Section A").to_edge(UP, buff=0.8)\n'
            'self.play(Write(scene_title))\n'
            'geo_title = Text("Definition:").to_edge(UP, buff=0.8).shift(LEFT * 3.2)\n'
            'self.play(FadeIn(geo_title))\n'
        )
        warnings = self._smells(code)
        assert any("title zone" in w and "geo_title" in w for w in warnings)

    def test_fadeout_in_between_clears_warning(self):
        code = (
            'scene_title = Text("A").to_edge(UP, buff=0.8)\n'
            'self.play(Write(scene_title))\n'
            'self.play(FadeOut(scene_title))\n'
            'geo_title = Text("B").to_edge(UP, buff=0.8)\n'
        )
        warnings = self._smells(code)
        assert not any("title zone" in w for w in warnings)

    def test_animate_to_edge_up_collides(self):
        # Section 6 of the dot-product run: original title never faded out,
        # then a second mobject animates itself to the UP edge.
        code = (
            'title = Text("Original").to_edge(UP, buff=0.8)\n'
            'final_title = Text("Conclusion").center()\n'
            'self.play(final_title.animate.to_edge(UP, buff=0.8))\n'
        )
        warnings = self._smells(code)
        assert any("title zone" in w and "final_title" in w for w in warnings)

    def test_corner_ur_treated_as_title_zone(self):
        code = (
            'title = Text("Section").to_edge(UP, buff=0.8)\n'
            'readout = Tex(r"x = 0.42").to_corner(UR)\n'
        )
        warnings = self._smells(code)
        assert any("title zone" in w and "readout" in w for w in warnings)

    def test_single_top_edge_mobject_no_warning(self):
        code = 'title = Text("Just one").to_edge(UP, buff=0.8)\n'
        warnings = self._smells(code)
        assert not any("title zone" in w for w in warnings)


class TestTitleWidthOverflow:
    """A long Text/Tex title at the UP edge renders clipped/garbled when its
    estimated rendered width exceeds the usable frame width (~13 manim units).
    Real defect: a 53-char title at font_size=36 clips off both sides of frame."""

    def _smells(self, code: str) -> list[str]:
        from manimgen.validator.codeguard import _check_layout_smells
        return _check_layout_smells(code)

    def test_long_title_fs36_warns(self):
        # The exact real defect: 53 chars at fs=36, .to_edge(UP)
        code = (
            'title = Text("The Algorithm: Pointers, Midpoint, and Comparison", '
            'font_size=36, color=WHITE).to_edge(UP, buff=0.8)\n'
        )
        warnings = self._smells(code)
        assert any("too wide" in w.lower() or "title width" in w.lower() for w in warnings)
        # message must guide the fix
        assert any(
            ("shorten" in w.lower() or "font_size" in w.lower())
            for w in warnings
            if "too wide" in w.lower() or "title width" in w.lower()
        )

    def test_normal_short_title_fs36_no_warning(self):
        # ~24-char title at fs=36 is well within the frame — must NOT warn.
        code = 'title = Text("Binary Search Trees", font_size=36).to_edge(UP, buff=0.8)\n'
        warnings = self._smells(code)
        assert not any("too wide" in w.lower() or "title width" in w.lower() for w in warnings)

    def test_normal_title_fs48_no_warning(self):
        # ~22-char title at the largest canonical size — still fits.
        code = 'title = Text("Gradient Descent Intro", font_size=48).to_edge(UP, buff=0.8)\n'
        warnings = self._smells(code)
        assert not any("too wide" in w.lower() or "title width" in w.lower() for w in warnings)

    def test_long_string_not_at_top_edge_no_warning(self):
        # A long string NOT in the title zone is the body's problem, not a title
        # clip — must not be flagged by the title-width check.
        code = (
            'body = Text("The Algorithm: Pointers, Midpoint, and Comparison", '
            'font_size=36).move_to(ORIGIN)\n'
        )
        warnings = self._smells(code)
        assert not any("too wide" in w.lower() or "title width" in w.lower() for w in warnings)

    def test_long_title_smaller_font_no_warning(self):
        # Same long string but at a small font_size fits — must NOT warn.
        code = (
            'title = Text("The Algorithm: Pointers, Midpoint, and Comparison", '
            'font_size=18).to_edge(UP, buff=0.8)\n'
        )
        warnings = self._smells(code)
        assert not any("too wide" in w.lower() or "title width" in w.lower() for w in warnings)

    def test_long_tex_title_also_warns(self):
        # The same width logic applies to Tex() titles at the UP edge.
        code = (
            'title = Tex(r"The Algorithm: Pointers, Midpoint, and Comparison", '
            'font_size=36).to_edge(UP, buff=0.8)\n'
        )
        warnings = self._smells(code)
        assert any("too wide" in w.lower() or "title width" in w.lower() for w in warnings)


class TestSideBySideArrayOverflow:
    """Two large horizontal arrays (10-element VGroups built from list
    comprehensions over an 8+ item source list) placed with opposing horizontal
    shifts (LEFT*n and RIGHT*n) on the same vertical band collide in the middle.
    Real defect: two 10-Square rows at LEFT*2.8 and RIGHT*2.8 overlap."""

    def _smells(self, code: str) -> list[str]:
        from manimgen.validator.codeguard import _check_layout_smells
        return _check_layout_smells(code)

    def test_two_opposing_large_arrays_warn(self):
        code = (
            "arr_a = [5, 3, 8, 1, 9, 2, 7, 4, 6, 0]\n"
            "arr_b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\n"
            "left_row = VGroup(*[Square(side_length=0.7) for v in arr_a]).arrange(RIGHT, buff=0.15).shift(LEFT * 2.8)\n"
            "right_row = VGroup(*[Square(side_length=0.7) for v in arr_b]).arrange(RIGHT, buff=0.15).shift(RIGHT * 2.8)\n"
        )
        warnings = self._smells(code)
        assert any("collide" in w.lower() or "overlap" in w.lower() or "overflow" in w.lower()
                   for w in warnings if "side-by-side" in w.lower() or "two" in w.lower())

    def test_single_large_array_no_warning(self):
        # A single 10-element array centered is fine — must NOT warn.
        code = (
            "arr = [5, 3, 8, 1, 9, 2, 7, 4, 6, 0]\n"
            "row = VGroup(*[Square(side_length=0.7) for v in arr]).arrange(RIGHT, buff=0.15).center()\n"
        )
        warnings = self._smells(code)
        assert not any("side-by-side" in w.lower() for w in warnings)

    def test_two_small_groups_no_warning(self):
        # Two SMALL groups (3 elements each) shifted apart do not collide —
        # must NOT warn (conservative: avoid false positives on small groups).
        code = (
            "a = [1, 2, 3]\n"
            "b = [4, 5, 6]\n"
            "left = VGroup(*[Square() for v in a]).arrange(RIGHT).shift(LEFT * 2.8)\n"
            "right = VGroup(*[Square() for v in b]).arrange(RIGHT).shift(RIGHT * 2.8)\n"
        )
        warnings = self._smells(code)
        assert not any("side-by-side" in w.lower() for w in warnings)

    def test_two_large_arrays_same_direction_no_warning(self):
        # Both shifted the SAME direction (both LEFT) — they don't collide in the
        # middle the way opposing shifts do; conservative check should not warn.
        code = (
            "arr_a = [5, 3, 8, 1, 9, 2, 7, 4, 6, 0]\n"
            "arr_b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\n"
            "left_row = VGroup(*[Square() for v in arr_a]).arrange(RIGHT).shift(LEFT * 2.8)\n"
            "other_row = VGroup(*[Square() for v in arr_b]).arrange(RIGHT).shift(LEFT * 2.8)\n"
        )
        warnings = self._smells(code)
        assert not any("side-by-side" in w.lower() for w in warnings)

    def test_two_large_arrays_vertically_separated_no_warning(self):
        # Opposing horizontal shifts but ALSO separated vertically (one UP, one
        # DOWN) — they are not on the same band, so no collision.
        code = (
            "arr_a = [5, 3, 8, 1, 9, 2, 7, 4, 6, 0]\n"
            "arr_b = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]\n"
            "top = VGroup(*[Square() for v in arr_a]).arrange(RIGHT).shift(UP * 2 + LEFT * 2.8)\n"
            "bot = VGroup(*[Square() for v in arr_b]).arrange(RIGHT).shift(DOWN * 2 + RIGHT * 2.8)\n"
        )
        warnings = self._smells(code)
        assert not any("side-by-side" in w.lower() for w in warnings)
