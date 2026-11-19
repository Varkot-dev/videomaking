"""Auto-fixes must not rewrite code that was already valid ManimGL.

Each case is valid input that a regex-based fix used to change into something
that still parsed but crashed at render time or showed the wrong thing.
"""

import ast

import pytest

from manimgen.validator.codeguard import (
    _fix_color_gradient_int_cast,
    _fix_font_size_to_scale,
    _remove_font_kwarg_from_tex,
    apply_error_aware_fixes,
    apply_known_fixes,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "line",
    [
        "self.play(FadeIn(title, UP))",
        "self.play(FadeOut(title, DOWN))",
        "self.play(FadeIn(title, shift=UP))",
        "anim = FadeOut(a, b)",
        "self.play(FadeIn(title, 0.5 * UP))",
    ],
)
def test_valid_fade_forms_are_not_split(line):
    assert apply_known_fixes(line + "\n")[0] == line + "\n"


def test_two_mobject_fade_is_still_split():
    fixed, applied = apply_known_fixes("self.play(FadeOut(a, b), run_time=1)\n")
    assert fixed == "self.play(FadeOut(a), FadeOut(b), run_time=1)\n"
    assert any("FadeOut" in a for a in applied)


def test_fade_split_handles_attribute_arguments():
    fixed, _ = apply_known_fixes("self.play(FadeIn(self.a, self.b))\n")
    assert fixed == "self.play(FadeIn(self.a), FadeIn(self.b))\n"


@pytest.mark.parametrize(
    "line",
    [
        "x = Text('Create (or reuse) a node')",
        'x = Text("Use MathTex (not here)")',
        "# Create (the) thing",
    ],
)
def test_api_renames_do_not_touch_strings_or_comments(line):
    assert apply_known_fixes(line + "\n")[0] == line + "\n"


def test_api_renames_still_apply_to_code():
    fixed, _ = apply_known_fixes("self.play(Create(MathTex('x')))\n")
    assert fixed == "self.play(ShowCreation(Tex('x')))\n"


def test_background_hex_is_not_remapped():
    line = 'mask = Rectangle(fill_color="#1C1C1C", fill_opacity=1)\n'
    assert apply_known_fixes(line)[0] == line


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("color_gradient((RED, BLUE), 5)", "color_gradient((RED, BLUE), 5)"),
        ("color_gradient([RED, BLUE], length=5)", "color_gradient([RED, BLUE], length=5)"),
        ("color_gradient([RED, BLUE], n / 2)", "color_gradient([RED, BLUE], int(n / 2))"),
        ("color_gradient((RED, BLUE), len(xs) * 1.5)", "color_gradient((RED, BLUE), int(len(xs) * 1.5))"),
        ("color_gradient([RED, BLUE], int(n))", "color_gradient([RED, BLUE], int(n))"),
    ],
)
def test_color_gradient_cast(code, expected):
    assert _fix_color_gradient_int_cast(code)[0] == expected


def test_transform_rewrite_keeps_nested_calls_whole():
    code = "self.play(Transform(VGroup(a, b), c), run_time=2)\n"
    fixed, _ = apply_error_aware_fixes(
        code, "ValueError: could not broadcast input array from shape (4,3)"
    )
    ast.parse(fixed)
    assert "FadeOut(VGroup(a, b))" in fixed
    assert "FadeIn(c)" in fixed


def test_font_kwarg_strip_respects_word_boundary():
    code = "TexText('hello', t2c_font='A')"
    assert _remove_font_kwarg_from_tex(code)[0] == code


def test_fractional_font_size_is_left_alone():
    code = "Text('x', font_size=30.5)"
    assert _fix_font_size_to_scale(code)[0] == code


def test_mobject_explosion_check_ignores_unrelated_range():
    from manimgen.validator.invariants import _I1_mobject_explosion

    code = (
        "dots = VGroup(*[Dot(p) for p in pts])\n"
        "for i in range(100):\n"
        "    samples.append(i)\n"
        "v = np.array([0, 1, 0])\n"
    )
    assert _I1_mobject_explosion(code) == []


def test_mobject_explosion_check_still_fires():
    from manimgen.validator.invariants import _I1_mobject_explosion

    assert _I1_mobject_explosion("g = VGroup(*[Dot() for _ in range(200)])\n")
    assert not _I1_mobject_explosion("g = VGroup(*[Dot() for _ in range(16)])\n")
