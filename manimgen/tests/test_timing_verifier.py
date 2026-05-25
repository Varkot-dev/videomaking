"""Tests for the static timing verifier."""
import ast
import textwrap

import pytest

from manimgen.validator.timing_verifier import (
    _FREEZE_BLOCK_THRESHOLD,
    UNKNOWN,
    CueTiming,
    _eval_constant,
    _get_run_time,
    _get_wait_duration,
    _split_into_cue_blocks,
    _time_for_statements,
    _Unknown,
    auto_fix_timing,
    blocking_freezes,
    join_frozen_with_timing,
    verify_timing,
)

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
    """_time_for_statements now returns (total, has_unknown) (issue #23).

    These assertions were updated for the new tuple contract — the durations
    they check are unchanged; only the return shape changed to carry the
    tri-state flag. None of these encoded the old asymmetric-default bug.
    """

    def _parse_body(self, code: str):
        return ast.parse(textwrap.dedent(code)).body

    def test_single_play(self):
        total, unknown = _time_for_statements(
            self._parse_body("self.play(Write(x), run_time=1.5)")
        )
        assert total == pytest.approx(1.5)
        assert unknown is False

    def test_play_default_runtime(self):
        total, unknown = _time_for_statements(self._parse_body("self.play(Write(x))"))
        assert total == pytest.approx(1.0)
        assert unknown is False

    def test_wait(self):
        total, unknown = _time_for_statements(self._parse_body("self.wait(2.5)"))
        assert total == pytest.approx(2.5)
        assert unknown is False

    def test_play_plus_wait(self):
        code = """\
            self.play(Write(x), run_time=1.5)
            self.wait(2.7)
        """
        total, unknown = _time_for_statements(self._parse_body(code))
        assert total == pytest.approx(4.2)
        assert unknown is False

    def test_for_loop(self):
        code = """\
            for i in range(5):
                self.play(ShowCreation(obj), run_time=0.2)
        """
        total, unknown = _time_for_statements(self._parse_body(code))
        assert total == pytest.approx(1.0)
        assert unknown is False

    def test_for_loop_range_start_stop(self):
        code = """\
            for i in range(1, 7):
                self.play(ShowCreation(obj), run_time=0.25)
        """
        total, unknown = _time_for_statements(self._parse_body(code))
        assert total == pytest.approx(1.5)
        assert unknown is False

    def test_multiple_plays(self):
        code = """\
            self.play(Write(title), run_time=0.6)
            self.play(ShowCreation(axes), run_time=0.8)
            self.play(ShowCreation(curve), run_time=2.0)
            self.wait(0.6)
        """
        total, unknown = _time_for_statements(self._parse_body(code))
        assert total == pytest.approx(4.0)
        assert unknown is False

    def test_if_branch_takes_max(self):
        code = """\
            if True:
                self.play(Write(x), run_time=2.0)
            else:
                self.play(Write(y), run_time=1.0)
        """
        total, unknown = _time_for_statements(self._parse_body(code))
        assert total == pytest.approx(2.0)
        assert unknown is False


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
            for i in range(10):
                self.play(ShowCreation(scan_rect), run_time=0.3)
            self.wait(4.0 - 0.3)
        """)
        # loop = 10 * 0.3 = 3.0, wait = 3.7 → total = 6.7, expected = 4.0 → 2.7s over
        # Tolerance is 1.0s — a 2.7s overrun must still trip verification.
        result = verify_timing(code, [4.0])
        assert not result["ok"]
        assert result["cues"][0].diff < -1.0  # scene is over-long by more than tolerance

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


# -----------------------------------------------------------------------
# auto_fix_timing — issue #22: deterministic insert-missing-wait resolver.
# When a cue block falls short of its expected duration and has NO self.wait()
# to adjust, the verifier inserts self.wait(residual) at the cue boundary
# instead of no-op'ing and handing the LLM an advisory hint.
# -----------------------------------------------------------------------


class TestInsertMissingWait:
    def test_inserts_wait_when_none_exists(self):
        code = textwrap.dedent("""\
            # CUE 0 — 10.0s
            self.play(ShowCreation(curve), run_time=2.0)
        """)
        fixed, applied = auto_fix_timing(code, [10.0])
        assert len(applied) == 1
        assert "inserted self.wait" in applied[0]
        assert "self.wait(8.00)" in fixed
        # The fix is authoritative: re-verify is now within tolerance.
        assert verify_timing(fixed, [10.0])["ok"]

    def test_insert_preserves_indentation(self):
        # 8-space construct-body indentation (after dedent strips the common
        # leading whitespace). The inserted wait must match it, not column 0.
        code = textwrap.dedent("""\
            class S(Scene):
                def construct(self):
                    # CUE 0 — 10.0s
                    self.play(Write(title), run_time=2.0)
            """)
        fixed, applied = auto_fix_timing(code, [10.0])
        assert len(applied) == 1
        assert "        self.wait(8.00)" in fixed
        # No column-0 wait was emitted.
        assert "\nself.wait(8.00)" not in fixed
        assert verify_timing(fixed, [10.0])["ok"]

    def test_no_insert_when_on_time(self):
        # 1.0s animation, 1.0s expected — no shortfall, nothing to insert.
        code = textwrap.dedent("""\
            # CUE 0 — 1.0s
            self.play(Write(x), run_time=1.0)
        """)
        fixed, applied = auto_fix_timing(code, [1.0])
        assert applied == []
        assert fixed == code

    def test_no_insert_below_tolerance(self):
        # 0.6s short — within the 1.0s tolerance band; the muxer pads it and
        # inserting a sub-second wait would be churn.
        code = textwrap.dedent("""\
            # CUE 0 — 1.6s
            self.play(Write(x), run_time=1.0)
        """)
        fixed, applied = auto_fix_timing(code, [1.6])
        assert applied == []
        assert fixed == code

    def test_no_insert_when_dynamic_duration(self):
        # Dynamic run_time → residual unknowable (#23). Must NOT fabricate a
        # wait that could over/undershoot a deliberate dynamic animation.
        code = textwrap.dedent("""\
            # CUE 0 — 10.0s
            self.play(ShowCreation(curve), run_time=rt)
        """)
        fixed, applied = auto_fix_timing(code, [10.0])
        assert applied == []
        assert fixed == code

    def test_no_insert_when_overrun(self):
        # animations already exceed expected — nothing to fill.
        code = textwrap.dedent("""\
            # CUE 0 — 1.0s
            self.play(Write(x), run_time=2.0)
            self.play(Grow(y), run_time=2.0)
        """)
        fixed, applied = auto_fix_timing(code, [1.0])
        assert applied == []
        assert fixed == code

    def test_insert_does_not_create_phantom_cue_block(self):
        # The CUE_FILL comment must not be parsed as a new "# CUE N" boundary.
        code = textwrap.dedent("""\
            # CUE 0 — 10.0s
            self.play(ShowCreation(curve), run_time=2.0)

            # CUE 1 — 4.0s
            self.play(Write(label), run_time=1.0)
            self.wait(3.0)
        """)
        fixed, _ = auto_fix_timing(code, [10.0, 4.0])
        assert len(_split_into_cue_blocks(fixed)) == 2
        assert verify_timing(fixed, [10.0, 4.0])["ok"]

    def test_insert_in_earlier_block_keeps_later_block_intact(self):
        # Reverse-order processing + rfind anchoring must not corrupt offsets:
        # both short no-wait cues should be filled and re-verify clean.
        code = textwrap.dedent("""\
            # CUE 0 — 8.0s
            self.play(ShowCreation(a), run_time=1.0)

            # CUE 1 — 6.0s
            self.play(ShowCreation(b), run_time=1.5)
        """)
        fixed, applied = auto_fix_timing(code, [8.0, 6.0])
        assert len(applied) == 2
        assert verify_timing(fixed, [8.0, 6.0])["ok"]

    def test_existing_wait_path_still_adjusts_not_inserts(self):
        # Sanity: when a wait DOES exist, we adjust it (one fix) rather than
        # inserting a second wait.
        code = textwrap.dedent("""\
            # CUE 0 — 6.0s
            self.play(Write(title), run_time=1.0)
            self.wait(2.0)
        """)
        fixed, applied = auto_fix_timing(code, [6.0])
        assert len(applied) == 1
        assert "adjusted" in applied[0]
        assert "inserted" not in applied[0]
        assert fixed.count("self.wait") == 1


# -----------------------------------------------------------------------
# apply_timing_gate — the single authoritative timing gate shared by
# retry.py and cli.py. Non-empty returned warnings trigger the cli.py
# zero-cost pre-render gate (skip first render, route to retry).
# -----------------------------------------------------------------------


class TestApplyTimingGate:
    def test_clean_code_passes_through_no_warnings(self, tmp_path):
        from manimgen.validator.retry import apply_timing_gate

        code = textwrap.dedent("""\
            # CUE 0 — 2.0s
            self.play(Write(title), run_time=1.0)
            self.wait(1.0)
        """)
        scene_path = str(tmp_path / "scene.py")
        with open(scene_path, "w") as f:
            f.write(code)

        out_code, warnings = apply_timing_gate(code, scene_path, [2.0])
        assert warnings == []
        assert out_code == code

    def test_dynamic_runtime_does_NOT_gate_render(self, tmp_path):
        """REWRITTEN for issue #23.

        The original test asserted that a ``run_time=rt`` scene must surface
        gating warnings and route to the retry path. That assertion *encoded
        the regression*: a dynamic-but-correct scene was being force-retried
        (and rewritten into a broken one). Post-#23 a cue with only dynamic
        durations is UNKNOWN, verify_timing reports ok=True, and
        apply_timing_gate returns no gating warnings — the render proceeds.
        The pre-existing assertion was asserting the bug, so it is replaced
        with the correct contract.
        """
        from manimgen.validator.retry import apply_timing_gate

        code = textwrap.dedent("""\
            # CUE 0 — 5.0s
            rt = compute_runtime()
            self.play(Write(title), run_time=rt)
        """)
        scene_path = str(tmp_path / "scene.py")
        with open(scene_path, "w") as f:
            f.write(code)

        out_code, warnings = apply_timing_gate(code, scene_path, [5.0])
        assert warnings == [], (
            "a purely-dynamic (UNKNOWN) cue must NOT gate the render — gating it "
            "is the #23 false-freeze regression"
        )
        # The code must be left byte-for-byte intact (no destructive auto-fix).
        assert out_code == code


class TestTriStateDurations:
    """Issue #23 regression suite — the active regression that rewrites correct
    scenes into broken ones.

    A correct scene using ``run_time=rt`` (a variable) was scored as a
    multi-second shortfall because dynamic durations were silently coerced
    (run_time→1.0, wait→0.0, asymmetrically). The tri-state fix marks them
    UNKNOWN; UNKNOWN cues are never flagged as freezes. A genuinely short
    *constant* wait after a long animation MUST still be flagged.
    """

    # --- low-level extractors return the UNKNOWN sentinel, not 0.0/1.0 ---

    def test_get_run_time_dynamic_is_unknown(self):
        call = ast.parse("self.play(Write(x), run_time=rt)").body[0].value
        assert _get_run_time(call) is UNKNOWN

    def test_get_run_time_constant_is_float(self):
        call = ast.parse("self.play(Write(x), run_time=2.0)").body[0].value
        assert _get_run_time(call) == pytest.approx(2.0)

    def test_get_run_time_omitted_is_default(self):
        call = ast.parse("self.play(Write(x))").body[0].value
        assert _get_run_time(call) == pytest.approx(1.0)

    def test_get_wait_dynamic_is_unknown_not_zero(self):
        # The asymmetric bug: this used to return 0.0, fabricating a shortfall.
        call = ast.parse("self.wait(hold)").body[0].value
        assert _get_wait_duration(call) is UNKNOWN

    def test_get_wait_constant_is_float(self):
        call = ast.parse("self.wait(2.0)").body[0].value
        assert _get_wait_duration(call) == pytest.approx(2.0)

    def test_get_wait_bare_is_one(self):
        call = ast.parse("self.wait()").body[0].value
        assert _get_wait_duration(call) == pytest.approx(1.0)

    # --- propagation through _time_for_statements ---

    def test_dynamic_runtime_propagates_unknown(self):
        stmts = ast.parse("self.play(Write(x), run_time=rt)").body
        total, unknown = _time_for_statements(stmts)
        assert unknown is True
        assert total == pytest.approx(0.0)  # only a lower bound

    def test_dynamic_wait_propagates_unknown(self):
        stmts = ast.parse("self.wait(hold)").body
        total, unknown = _time_for_statements(stmts)
        assert unknown is True

    def test_dynamic_loop_count_propagates_unknown(self):
        code = textwrap.dedent("""\
            for i in range(n):
                self.play(ShowCreation(obj), run_time=0.2)
        """)
        total, unknown = _time_for_statements(ast.parse(code).body)
        assert unknown is True
        # known per-iteration time still counted once as a lower bound
        assert total == pytest.approx(0.2)

    def test_mixed_known_and_unknown(self):
        code = textwrap.dedent("""\
            self.play(Write(x), run_time=1.5)
            self.play(Grow(y), run_time=rt)
            self.wait(2.0)
        """)
        total, unknown = _time_for_statements(ast.parse(code).body)
        assert unknown is True
        assert total == pytest.approx(3.5)  # 1.5 + 2.0, dynamic rt excluded

    # --- THE REGRESSION: a correct dynamic scene must NOT be flagged ---

    def test_dynamic_runtime_scene_not_flagged_as_freeze(self):
        """A scene that drives a long animation with a variable run_time and
        holds for a computed duration is CORRECT. Pre-fix it was scored as a
        ~9s freeze (run_time→1.0, the hold→0.0) and force-retried, rewriting a
        good scene into a broken one. It must now pass clean."""
        code = textwrap.dedent("""\
            # CUE 0 — 9.5s
            rt = 7.5
            hold = 2.0
            self.play(ShowCreation(curve), run_time=rt)
            self.wait(hold)
        """)
        result = verify_timing(code, [9.5])
        ct = result["cues"][0]
        assert ct.has_unknown is True
        assert ct.ok is True  # UNKNOWN cue is acceptable, not a mismatch
        # And crucially: it does NOT enter the blocking-freeze set.
        assert blocking_freezes(result) == []

    def test_unknown_cue_ok_property(self):
        ct = CueTiming(cue_index=0, expected=9.5, computed=0.0, has_unknown=True)
        # diff would be +9.5 (huge), but has_unknown overrides → ok
        assert ct.diff == pytest.approx(9.5)
        assert ct.ok is True

    def test_blocking_freezes_ignores_unknown_even_with_huge_diff(self):
        cues = [CueTiming(cue_index=0, expected=12.0, computed=0.0, has_unknown=True)]
        assert blocking_freezes({"ok": False, "cues": cues, "warnings": []}) == []

    # --- THE OTHER HALF: a genuine short CONSTANT wait IS still flagged ---

    def test_genuine_short_constant_wait_still_flagged(self):
        """A tiny constant wait after a long animation is a real freeze and
        MUST still block — the fix must not disarm the genuine detector."""
        code = textwrap.dedent("""\
            # CUE 0 — 10.0s
            self.play(ShowCreation(curve), run_time=2.0)
            self.wait(0.01)
        """)
        result = verify_timing(code, [10.0])
        ct = result["cues"][0]
        assert ct.has_unknown is False
        assert ct.ok is False
        assert ct.diff == pytest.approx(7.99)
        blocked = blocking_freezes(result)
        assert len(blocked) == 1
        assert "CUE 0" in blocked[0]

    def test_unknown_cue_emits_informational_warning_only(self):
        code = textwrap.dedent("""\
            # CUE 0 — 5.0s
            self.play(Write(t), run_time=rt)
        """)
        result = verify_timing(code, [5.0])
        # ok at the cue level (UNKNOWN), warning is informational, not a freeze.
        assert result["cues"][0].has_unknown is True
        assert any("dynamic duration" in w for w in result["warnings"])
        assert not any("freeze-frame tail" in w for w in result["warnings"])

    def test_unknown_sentinel_is_singleton(self):
        assert isinstance(UNKNOWN, _Unknown)
        assert _get_run_time(
            ast.parse("self.play(x, run_time=a)").body[0].value
        ) is UNKNOWN


class TestBlockingFreezes:
    """The hard timing gate: a cue whose animation finishes >= the threshold
    before its narration is a multi-second dead screen that must BLOCK render
    acceptance. Sub-threshold shortfalls and overruns must NOT block."""

    def _result(self, cues):
        # mimic verify_timing's return shape
        return {"ok": False, "cues": cues, "warnings": []}

    def test_threshold_default_is_2_5s(self):
        assert _FREEZE_BLOCK_THRESHOLD == 2.5

    def test_big_freeze_tail_is_blocking(self):
        # animation 5.5s vs narration 9.5s -> 4.0s frozen tail (the real bug)
        cues = [CueTiming(cue_index=0, expected=9.5, computed=5.5)]
        blocked = blocking_freezes(self._result(cues))
        assert len(blocked) == 1
        assert "CUE 0" in blocked[0]
        assert "4.00s frozen tail" in blocked[0]

    def test_sub_threshold_shortfall_not_blocking(self):
        # 1.5s short — annoying but below the 2.5s block threshold; the muxer
        # pads it and retrying for it caused documented thrash.
        cues = [CueTiming(cue_index=0, expected=6.0, computed=4.5)]
        assert blocking_freezes(self._result(cues)) == []

    def test_overrun_not_blocking(self):
        # animation LONGER than narration (negative diff) — muxer trims video
        # to audio; not a dead screen, must not block.
        cues = [CueTiming(cue_index=1, expected=4.0, computed=9.0)]
        assert blocking_freezes(self._result(cues)) == []

    def test_only_offending_cues_reported(self):
        cues = [
            CueTiming(cue_index=0, expected=5.0, computed=4.8),  # fine
            CueTiming(cue_index=1, expected=12.0, computed=3.0),  # 9s freeze
            CueTiming(cue_index=2, expected=6.0, computed=10.0),  # overrun
        ]
        blocked = blocking_freezes(self._result(cues))
        assert len(blocked) == 1
        assert "CUE 1" in blocked[0]

    def test_empty_result_is_safe(self):
        assert blocking_freezes({"ok": True, "cues": [], "warnings": []}) == []
        assert blocking_freezes({}) == []


class TestJoinFrozenWithTiming:
    """#32: the timing ∧ frame join that re-admits the frozen-frame signal
    that retry.py used to discard outright. A frozen frame becomes a HARD issue
    IFF the timing oracle independently confirms a dead tail; for a legit
    narration hold it is dropped (no false-positive)."""

    _FROZEN = (
        "ISSUE: Frames at t=2.0s and t=4.0s are 99% identical — "
        "animation appears frozen | CAUSE: long wait | FIX: add activity"
    )
    _BLACK = "ISSUE: Black/empty frame at t=2.0s | CAUSE: faded out | FIX: keep content"
    _CLIP = "ISSUE: Content near top edge at t=2.0s — element may be cut off"

    def test_frozen_kept_when_timing_confirms_dead_tail(self):
        # frozen frame + timing-confirmed freeze → HARD (kept).
        kept = join_frozen_with_timing([self._FROZEN], timing_freeze_confirmed=True)
        assert kept == [self._FROZEN]

    def test_frozen_dropped_when_timing_does_not_confirm(self):
        # frozen frame but narration still running (no blocking freeze) →
        # legit hold, dropped. This is the case that must NOT false-positive.
        kept = join_frozen_with_timing([self._FROZEN], timing_freeze_confirmed=False)
        assert kept == []

    def test_black_frame_always_kept_regardless_of_timing(self):
        # Black frames are not subject to the timing join — always pass through.
        assert join_frozen_with_timing([self._BLACK], timing_freeze_confirmed=False) == [
            self._BLACK
        ]
        assert join_frozen_with_timing([self._BLACK], timing_freeze_confirmed=True) == [
            self._BLACK
        ]

    def test_clipping_always_kept_regardless_of_timing(self):
        assert join_frozen_with_timing([self._CLIP], timing_freeze_confirmed=False) == [
            self._CLIP
        ]

    def test_mixed_issues_only_frozen_is_filtered(self):
        issues = [self._BLACK, self._FROZEN, self._CLIP]
        # No timing confirmation: drop only the frozen line, keep the rest.
        kept = join_frozen_with_timing(issues, timing_freeze_confirmed=False)
        assert kept == [self._BLACK, self._CLIP]
        # Timing confirms: keep everything.
        kept = join_frozen_with_timing(issues, timing_freeze_confirmed=True)
        assert kept == issues

    def test_empty_input_is_safe(self):
        assert join_frozen_with_timing([], timing_freeze_confirmed=True) == []
        assert join_frozen_with_timing([], timing_freeze_confirmed=False) == []
