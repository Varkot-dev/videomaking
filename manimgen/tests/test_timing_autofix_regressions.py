"""Regression tests for timing auto-fix edits that landed in the wrong place.

Every scenario here used to produce either an edit in the wrong cue, a wait
inside a loop body, or a file that no longer parsed.
"""

import ast

import pytest

from manimgen.validator import frame_checker
from manimgen.validator.timing_verifier import (
    _FROZEN_FRAME_MARKER,
    _strip_phantom_cue_blocks,
    auto_fix_timing,
    join_frozen_with_timing,
    verify_timing,
)

pytestmark = pytest.mark.unit


def _scene(body: str, extra: str = "") -> str:
    indented = "\n".join("        " + ln if ln else "" for ln in body.splitlines())
    return (
        "from manimlib import *\n\n\nclass S(Scene):\n"
        f"    def construct(self):\n{indented}\n{extra}"
    )


def _computed(code: str, durations: list[float]) -> list[float]:
    return [c.computed for c in verify_timing(code, durations)["cues"]]


def test_frozen_marker_matches_what_frame_checker_emits():
    """The join must recognise the real issue line, not a retyped copy of it."""
    line = (
        f"ISSUE: Frames at t=1.0s and t=2.0s are 100.00% identical - "
        f"{frame_checker.FROZEN_FRAME_MARKER} | CAUSE: x | FIX: y"
    )
    assert _FROZEN_FRAME_MARKER in line
    assert join_frozen_with_timing([line], timing_freeze_confirmed=False) == []
    assert join_frozen_with_timing([line], timing_freeze_confirmed=True) == [line]


def test_frame_checker_source_uses_the_shared_marker():
    import inspect

    src = inspect.getsource(frame_checker)
    assert src.count('"animation appears frozen"') == 1  # the constant only


def test_wait_adjustment_edits_the_short_cue_not_a_later_identical_wait():
    code = _scene(
        "# CUE 0\n"
        "self.play(FadeIn(a), run_time=1.0)\n"
        "self.wait(1.0)\n"
        "# CUE 1\n"
        "self.play(FadeIn(b), run_time=3.0)\n"
        "self.wait(1.0)"
    )
    fixed, applied = auto_fix_timing(code, [5.0, 4.0])
    assert applied
    assert _computed(fixed, [5.0, 4.0]) == pytest.approx([5.0, 4.0])


def test_wait_inside_a_loop_is_not_the_one_adjusted():
    code = _scene(
        "# CUE 0\n"
        "for i in range(3):\n"
        "    self.play(FadeIn(a), run_time=0.5)\n"
        "    self.wait(0.2)\n"
        "self.play(FadeIn(b), run_time=1.0)\n"
        "self.wait(2.0)"
    )
    fixed, _ = auto_fix_timing(code, [8.0])
    assert "self.wait(0.2)" in fixed
    assert _computed(fixed, [8.0]) == pytest.approx([8.0])


def test_fill_wait_goes_after_a_trailing_loop_not_inside_it():
    code = _scene(
        "# CUE 0\n"
        "for i in range(3):\n"
        "    self.play(FadeIn(a))"
    )
    fixed, applied = auto_fix_timing(code, [8.0])
    assert applied
    assert _computed(fixed, [8.0]) == pytest.approx([8.0])


def test_run_time_scaling_with_non_ascii_text_keeps_the_file_valid():
    code = _scene(
        "# CUE 0\n"
        'self.play(Write(Text("α → β — done")), run_time=1.0)\n'
        "self.play(FadeIn(a), run_time=1.0)"
    )
    fixed, applied = auto_fix_timing(code, [4.0])
    assert applied
    ast.parse(fixed)
    assert 'Text("α → β — done")' in fixed
    assert _computed(fixed, [4.0]) == pytest.approx([4.0])


def test_wait_with_nested_parentheses_is_replaced_whole():
    code = _scene(
        "# CUE 0\nself.play(FadeIn(a), run_time=1.0)\nself.wait((4.0 - 1.5) / 2)"
    )
    fixed, applied = auto_fix_timing(code, [6.0])
    assert applied
    ast.parse(fixed)
    assert _computed(fixed, [6.0]) == pytest.approx([6.0])


def test_a_wait_named_in_a_comment_is_not_edited():
    code = _scene(
        "# CUE 0\n"
        "self.play(FadeIn(a), run_time=1.0)\n"
        "self.wait(1.0)\n"
        "# hold: self.wait(1.0) keeps the frame"
    )
    fixed, _ = auto_fix_timing(code, [6.0])
    assert "# hold: self.wait(1.0) keeps the frame" in fixed
    assert _computed(fixed, [6.0]) == pytest.approx([6.0])


@pytest.mark.parametrize(
    ("loop", "count"),
    [("range(5, 0, -1)", 5), ("range(0, 10, 2)", 5), ("range(2, 5)", 3)],
)
def test_range_start_and_step_are_honoured(loop, count):
    code = _scene(f"# CUE 0\nfor i in {loop}:\n    self.wait(1.0)")
    result = verify_timing(code, [float(count)])
    assert result["cues"][0].computed == pytest.approx(count)
    assert result["ok"]


@pytest.mark.parametrize(
    "body",
    [
        "while t < 3:\n    self.play(FadeIn(a), run_time=1.0)",
        "try:\n    self.play(FadeIn(a), run_time=1.0)\nexcept Exception:\n    pass",
        "self.play(Write(b, run_time=2.0))",
        "self.build_grid()",
    ],
)
def test_untimeable_constructs_are_unknown_and_left_alone(body):
    extra = (
        "\n    def build_grid(self):\n"
        "        self.play(FadeIn(a), run_time=4.0)\n"
    )
    code = _scene("# CUE 0\n" + body + "\nself.wait(1.0)", extra)
    result = verify_timing(code, [6.0])
    assert result["cues"][0].has_unknown
    assert auto_fix_timing(code, [6.0])[0] == code


def test_scaling_accounts_for_plays_it_cannot_scale():
    code = _scene(
        "# CUE 0\n"
        "self.play(FadeIn(a), run_time=1.0)\n"
        "self.play(FadeIn(b))\n"
        "self.play(FadeIn(c))"
    )
    fixed, applied = auto_fix_timing(code, [6.0])
    assert applied
    assert _computed(fixed, [6.0]) == pytest.approx([6.0], abs=0.01)


def test_helper_method_after_construct_survives_phantom_stripping():
    helper = "\n    def make_box(self, n):\n        return Square().scale(n)\n"
    code = _scene(
        "# CUE 0\n"
        "self.add(self.make_box(1))\n"
        "self.wait(2.0)\n"
        "# CUE 1\n"
        "self.wait(3.0)",
        helper,
    )
    fixed, notes = _strip_phantom_cue_blocks(code, 1)
    assert notes
    assert "def make_box" in fixed
    ast.parse(fixed)


def test_last_cue_is_verified_when_a_helper_follows_construct():
    helper = "\n    def make_box(self, n):\n        return Square().scale(n)\n"
    code = _scene("# CUE 0\nself.wait(2.0)\n# CUE 1\nself.wait(3.0)", helper)
    result = verify_timing(code, [2.0, 3.0])
    assert [c.cue_index for c in result["cues"]] == [0, 1]
    assert not any("could not parse" in w for w in result["warnings"])
