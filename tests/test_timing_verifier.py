"""Tests for the static timing verifier."""
import textwrap

import pytest

from manimgen.validator.timing_verifier import (
    CueTiming,
    auto_fix_timing,
    verify_timing,
    _eval_constant,
    _split_into_cue_blocks,
    _time_for_statements,
)
import ast


# -----------------------------------------------------------------------
# _eval_constant
# -----------------------------------------------------------------------

class TestEvalConstant:
    def test_int(self):
        node = ast.parse("42").body[0].value
        assert _eval_constant(node) == 42.0

    def test_float(self):
        node = ast.parse("3.14").body[0].value
        assert _eval_constant(node) == pytest.approx(3.14)

    def test_negative(self):
        node = ast.parse("-1.5").body[0].value
        assert _eval_constant(node) == pytest.approx(-1.5)

    def test_add(self):
        node = ast.parse("1.5 + 2.7").body[0].value
        assert _eval_constant(node) == pytest.approx(4.2)

    def test_sub(self):
        node = ast.parse("6.0 - 1.5").body[0].value
        assert _eval_constant(node) == pytest.approx(4.5)

    def test_mult(self):
        node = ast.parse("0.25 * 6").body[0].value
        assert _eval_constant(node) == pytest.approx(1.5)

    def test_max(self):
        node = ast.parse("max(0.01, 3.5 - 4.0)").body[0].value
        assert _eval_constant(node) == pytest.approx(0.01)

    def test_unresolvable_variable(self):
        node = ast.parse("x + 1").body[0].value
        assert _eval_constant(node) is None


# -----------------------------------------------------------------------
# _split_into_cue_blocks
# -----------------------------------------------------------------------

class TestSplitCueBlocks:
    def test_basic_split(self):
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)

            # CUE 1 — 5.0s
            self.play(ShowCreation(obj), run_time=2.0)
            self.wait(3.0)
        """)
        blocks = _split_into_cue_blocks(code)
        assert len(blocks) == 2
        assert blocks[0][0] == 0
        assert blocks[1][0] == 1
        assert "Write(title)" in blocks[0][1]
        assert "ShowCreation(obj)" in blocks[1][1]

    def test_no_cue_comments(self):
        code = "self.play(Write(x), run_time=1.0)\nself.wait(2.0)\n"
        blocks = _split_into_cue_blocks(code)
        assert len(blocks) == 1
        assert blocks[0][0] == 0

    def test_three_cues(self):
        code = "# CUE 0\na()\n# CUE 1\nb()\n# CUE 2\nc()\n"
        blocks = _split_into_cue_blocks(code)
        assert len(blocks) == 3
        assert [b[0] for b in blocks] == [0, 1, 2]


# -----------------------------------------------------------------------
# _time_for_statements
# -----------------------------------------------------------------------

class TestTimeForStatements:
    def _parse_body(self, code: str):
        return ast.parse(textwrap.dedent(code)).body

    def test_single_play(self):
        stmts = self._parse_body("self.play(Write(x), run_time=1.5)")
        assert _time_for_statements(stmts) == pytest.approx(1.5)

    def test_play_default_runtime(self):
        stmts = self._parse_body("self.play(Write(x))")
        assert _time_for_statements(stmts) == pytest.approx(1.0)

    def test_wait(self):
        stmts = self._parse_body("self.wait(2.5)")
        assert _time_for_statements(stmts) == pytest.approx(2.5)

    def test_play_plus_wait(self):
        code = """\
            self.play(Write(x), run_time=1.5)
            self.wait(2.7)
        """
        stmts = self._parse_body(code)
        assert _time_for_statements(stmts) == pytest.approx(4.2)

    def test_for_loop(self):
        code = """\
            for i in range(5):
                self.play(ShowCreation(obj), run_time=0.2)
        """
        stmts = self._parse_body(code)
        assert _time_for_statements(stmts) == pytest.approx(1.0)

    def test_for_loop_range_start_stop(self):
        code = """\
            for i in range(1, 7):
                self.play(ShowCreation(obj), run_time=0.25)
        """
        stmts = self._parse_body(code)
        assert _time_for_statements(stmts) == pytest.approx(1.5)

    def test_multiple_plays(self):
        code = """\
            self.play(Write(title), run_time=0.6)
            self.play(ShowCreation(axes), run_time=0.8)
            self.play(ShowCreation(curve), run_time=2.0)
            self.wait(0.6)
        """
        stmts = self._parse_body(code)
        assert _time_for_statements(stmts) == pytest.approx(4.0)

    def test_if_branch_takes_max(self):
        code = """\
            if True:
                self.play(Write(x), run_time=2.0)
            else:
                self.play(Write(y), run_time=1.0)
        """
        stmts = self._parse_body(code)
        assert _time_for_statements(stmts) == pytest.approx(2.0)


# -----------------------------------------------------------------------
# verify_timing — known-good scenes
# -----------------------------------------------------------------------

class TestVerifyTimingGood:
    def test_exact_match(self):
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)

            # CUE 1 — 5.0s
            self.play(ShowCreation(obj), run_time=2.0)
            self.wait(3.0)
        """)
        result = verify_timing(code, [3.0, 5.0])
        assert result["ok"]
        assert len(result["cues"]) == 2
        assert result["cues"][0].computed == pytest.approx(3.0)
        assert result["cues"][1].computed == pytest.approx(5.0)

    def test_within_tolerance(self):
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.3)
        """)
        # 3.3 vs 3.0 → diff = -0.3 → within 0.5 tolerance
        result = verify_timing(code, [3.0])
        assert result["ok"]

    def test_loop_timing(self):
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            for i in range(5):
                self.play(ShowCreation(scan_rect), run_time=0.2)
            self.wait(max(0.01, 4.0 - 1.0))
        """)
        # 5 * 0.2 = 1.0 + max(0.01, 3.0) = 3.0 → total = 4.0
        result = verify_timing(code, [4.0])
        assert result["ok"]
        assert result["cues"][0].computed == pytest.approx(4.0)


# -----------------------------------------------------------------------
# verify_timing — known-bad scenes
# -----------------------------------------------------------------------

class TestVerifyTimingBad:
    def test_timing_too_short(self):
        code = textwrap.dedent("""\
            # CUE 0 — 6.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)
        """)
        # Total = 3.0, expected = 6.0 → 3.0s short
        result = verify_timing(code, [6.0])
        assert not result["ok"]
        assert result["cues"][0].diff == pytest.approx(3.0)
        assert "short" in result["warnings"][0]

    def test_timing_too_long(self):
        code = textwrap.dedent("""\
            # CUE 0 — 2.0s
            self.play(Write(title), run_time=1.5)
            self.play(ShowCreation(obj), run_time=2.0)
            self.wait(1.0)
        """)
        # Total = 4.5, expected = 2.0 → 2.5s over
        result = verify_timing(code, [2.0])
        assert not result["ok"]
        assert "over" in result["warnings"][0]

    def test_loop_timing_undercount(self):
        """The classic bug: subtracting one iteration instead of all N."""
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            for i in range(5):
                self.play(ShowCreation(scan_rect), run_time=0.2)
            self.wait(4.0 - 0.2)
        """)
        # loop = 5 * 0.2 = 1.0, wait = 3.8 → total = 4.8, expected = 4.0 → 0.8s over
        result = verify_timing(code, [4.0])
        assert not result["ok"]
        assert result["cues"][0].diff < -0.5  # scene is over-long

    def test_no_cue_comments_is_not_failure(self):
        code = "self.play(Write(x), run_time=1.0)\nself.wait(2.0)\n"
        result = verify_timing(code, [3.0])
        # No CUE comments → single block at index 0 → should still verify
        assert result["ok"]


# -----------------------------------------------------------------------
# auto_fix_timing
# -----------------------------------------------------------------------

class TestAutoFixTiming:
    def test_adjusts_wait_for_short_cue(self):
        code = textwrap.dedent("""\
            # CUE 0 — 6.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)
        """)
        fixed, applied = auto_fix_timing(code, [6.0])
        assert len(applied) == 1
        assert "5.00" in fixed or "5.0" in fixed  # wait should be ~5.0

    def test_leaves_good_timing_alone(self):
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)
        """)
        fixed, applied = auto_fix_timing(code, [3.0])
        assert len(applied) == 0
        assert fixed == code

    def test_clamps_to_minimum(self):
        """If animations already exceed cue duration, wait should be 0.01."""
        code = textwrap.dedent("""\
            # CUE 0 — 2.0s
            self.play(Write(title), run_time=1.5)
            self.play(ShowCreation(obj), run_time=2.0)
            self.wait(1.0)
        """)
        fixed, applied = auto_fix_timing(code, [2.0])
        assert len(applied) == 1
        assert "0.01" in fixed

    def test_multiple_cues(self):
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.wait(1.0)

            # CUE 1 — 5.0s
            self.play(ShowCreation(obj), run_time=2.0)
            self.wait(1.0)
        """)
        fixed, applied = auto_fix_timing(code, [3.0, 5.0])
        assert len(applied) == 2  # both cues need adjustment
        result = verify_timing(fixed, [3.0, 5.0])
        # After fix, both should be within tolerance
        assert result["ok"]
