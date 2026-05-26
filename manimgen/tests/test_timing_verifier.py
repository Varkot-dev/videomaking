"""Tests for the static timing verifier."""
import ast
import textwrap

import pytest

from manimgen.validator.timing_verifier import (
    _FREEZE_BLOCK_THRESHOLD,
    _MIN_RUN_TIME,
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

    def test_identical_play_cues_each_get_their_own_wait(self):
        # Regression: two cues with byte-identical play text both need a wait
        # inserted (huge stretch routes to the fallback wait path). The wait for
        # CUE 0 must land in CUE 0, not in the later identical block (the old
        # rfind-across-whole-file bug put both waits in CUE 1).
        code = textwrap.dedent("""\
            # CUE 0 — 5.0s
            self.play(Write(title), run_time=1.0)

            # CUE 1 — 5.0s
            self.play(Write(title), run_time=1.0)
        """)
        fixed, applied = auto_fix_timing(code, [5.0, 5.0])
        # each cue block contains exactly one inserted wait
        import re as _re
        frags = _re.split(r"# CUE \d", fixed)[1:]  # drop preamble
        assert len(frags) == 2
        for frag in frags:
            assert frag.count("self.wait(") == 1, (
                "each cue must get its own wait, not both in one block"
            )
        ast.parse(fixed)

    def test_no_wait_inserted_when_overrun(self):
        # An overrun (4.0s animation vs 1.0s narration) must NOT get a self.wait()
        # inserted — there is nothing to *fill*. Since #59, the overrun is instead
        # COMPRESSED by scaling run_times to fit D; assert no wait was added and
        # the compression happened.
        code = textwrap.dedent("""\
            # CUE 0 — 1.0s
            self.play(Write(x), run_time=2.0)
            self.play(Grow(y), run_time=2.0)
        """)
        fixed, applied = auto_fix_timing(code, [1.0])
        assert "self.wait(" not in fixed, "no wait should be inserted on an overrun"
        # the run_times are compressed to fit the 1.0s narration
        assert "run_time=2.0" not in fixed
        assert any("compress" in m.lower() for m in applied)

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


# -----------------------------------------------------------------------
# auto_fix_timing — RUN_TIME SCALING (issue #59).
#
# The core fix: when a cue's animations sum to a different wall-clock than its
# narration duration D, scale every resolvable self.play(run_time=...) literal
# by factor = D / sum_of_play_run_time so the animation fills exactly D. This
# extends (does NOT replace) the existing self.wait() adjustment — scaling is
# tried first; waits only absorb a residual that scaling cannot.
#
# Constraints under test (from 4 adversarial design reviews):
#  - FULL AST traversal: every run_time literal scaled, including inside loops
#    and if-blocks (a naive rfind would fix only the last occurrence).
#  - UNKNOWN cues (dynamic run_time / dynamic loop count) are NOT scaled.
#  - Zero-play cues never divide by zero — fall back to wait insertion.
#  - Compression floor _MIN_RUN_TIME; below it, warn + best-effort, never drop.
#  - Idempotency: running twice == running once (factor within [0.98,1.02] no-op).
#  - Comments preserved; comments/strings containing "run_time=" never touched.
# -----------------------------------------------------------------------


def _runtime_literals(code: str) -> list[float]:
    """Extract every numeric run_time= literal from code, in source order.

    Used by the scaling tests to assert ALL occurrences were rewritten (not
    just the last), independent of how auto_fix_timing locates them.
    """
    out: list[float] = []
    tree = ast.parse(code)
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "play"
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "run_time" and isinstance(kw.value, ast.Constant):
                out.append(float(kw.value.value))
    return out


class TestRunTimeScalingShort:
    def test_short_cue_scales_run_times_up(self):
        # plays sum to 2.0, D=4.0 → factor 2x → run_times become 0.8 and 1.2.
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            self.play(Write(title), run_time=0.8)
            self.play(ShowCreation(curve), run_time=1.2)
        """)
        fixed, applied = auto_fix_timing(code, [4.0])
        assert applied, "a 2x-short cue must be scaled"
        lits = _runtime_literals(fixed)
        assert lits == pytest.approx([1.6, 2.4])
        # The cue now fills D exactly.
        assert verify_timing(fixed, [4.0])["ok"]

    def test_short_cue_remains_valid_python(self):
        code = textwrap.dedent("""\
            # CUE 0 — 6.0s
            self.play(Write(title), run_time=1.0)
            self.play(ShowCreation(curve), run_time=2.0)
        """)
        fixed, _ = auto_fix_timing(code, [6.0])
        ast.parse(fixed)  # must not raise
        assert verify_timing(fixed, [6.0])["ok"]


class TestRunTimeScalingLong:
    def test_long_cue_compresses_run_times(self):
        # plays sum to 6.0, D=3.0 → factor 0.5x → each run_time halved.
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=2.0)
            self.play(ShowCreation(curve), run_time=4.0)
        """)
        fixed, applied = auto_fix_timing(code, [3.0])
        assert applied, "a 2x-long cue must be compressed"
        lits = _runtime_literals(fixed)
        assert lits == pytest.approx([1.0, 2.0])
        assert verify_timing(fixed, [3.0])["ok"]


class TestRunTimeScalingLoop:
    def test_loop_run_time_literal_scaled(self):
        # for i in range(5): self.play(run_time=0.4) → contributes 5*0.4=2.0.
        # D=4.0 → factor 2x → the single literal 0.4 becomes 0.8 (5*0.8=4.0).
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            for i in range(5):
                self.play(ShowCreation(scan_rect), run_time=0.4)
        """)
        fixed, applied = auto_fix_timing(code, [4.0])
        assert applied
        lits = _runtime_literals(fixed)
        # Exactly one literal in source; it must be scaled to 0.8 — NOT left
        # at 0.4 and NOT a rogue per-occurrence overcorrection.
        assert lits == pytest.approx([0.8])
        assert verify_timing(fixed, [4.0])["ok"]

    def test_all_occurrences_inside_loop_and_outside_scaled(self):
        # A literal appears once before the loop and once inside it. A naive
        # rfind would touch only the last; the full-AST transform must scale
        # BOTH. Pre-time: 0.5 (outside) + 4*0.5 (loop) = 2.5. D=5.0 → 2x.
        code = textwrap.dedent("""\
            # CUE 0 — 5.0s
            self.play(Write(title), run_time=0.5)
            for i in range(4):
                self.play(ShowCreation(box), run_time=0.5)
        """)
        fixed, applied = auto_fix_timing(code, [5.0])
        assert applied
        lits = _runtime_literals(fixed)
        assert len(lits) == 2
        assert all(v == pytest.approx(1.0) for v in lits), (
            "every run_time occurrence must be scaled, not just the last"
        )
        assert verify_timing(fixed, [5.0])["ok"]


class TestRunTimeScalingUnknown:
    def test_dynamic_runtime_not_scaled_falls_back_to_wait(self):
        # run_time=rt is UNKNOWN: must NOT compute a scale factor from it. With
        # no wait to adjust and a dynamic duration, auto_fix is a clean no-op.
        code = textwrap.dedent("""\
            # CUE 0 — 8.0s
            self.play(ShowCreation(curve), run_time=rt)
        """)
        fixed, applied = auto_fix_timing(code, [8.0])
        assert applied == []
        assert fixed == code

    def test_dynamic_loop_count_not_scaled(self):
        # range(n) → loop count UNKNOWN → must not scale (would be wrong).
        code = textwrap.dedent("""\
            # CUE 0 — 8.0s
            for i in range(n):
                self.play(ShowCreation(box), run_time=0.3)
        """)
        fixed, applied = auto_fix_timing(code, [8.0])
        assert applied == []
        assert fixed == code

    def test_unknown_with_existing_wait_left_untouched(self):
        # Dynamic run_time + a constant trailing wait: the existing path skips
        # blocks with any dynamic duration. Must not scale, must not crash.
        code = textwrap.dedent("""\
            # CUE 0 — 8.0s
            self.play(ShowCreation(curve), run_time=rt)
            self.wait(1.0)
        """)
        fixed, applied = auto_fix_timing(code, [8.0])
        assert applied == []
        assert fixed == code


class TestRunTimeScalingZeroPlay:
    def test_zero_play_cue_no_division_by_zero(self):
        # No plays at all — only a wait. sum_of_play_run_time == 0 → must NOT
        # divide by zero; fall back to existing wait-insert/adjust to reach D.
        code = textwrap.dedent("""\
            # CUE 0 — 5.0s
            self.wait(1.0)
        """)
        fixed, applied = auto_fix_timing(code, [5.0])
        assert applied  # the wait is adjusted up to ~5.0
        assert verify_timing(fixed, [5.0])["ok"]
        ast.parse(fixed)

    def test_zero_play_no_wait_inserts_wait(self):
        # Pure ambient/no-play, no wait: the residual resolver inserts a wait.
        code = textwrap.dedent("""\
            # CUE 0 — 6.0s
            self.frame.add_ambient_rotation(angular_speed=0.2)
        """)
        fixed, applied = auto_fix_timing(code, [6.0])
        assert applied
        assert "self.wait" in fixed
        ast.parse(fixed)


class TestRunTimeScalingIdempotency:
    def test_scaling_twice_equals_once(self):
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            self.play(Write(title), run_time=0.8)
            self.play(ShowCreation(curve), run_time=1.2)
        """)
        once, applied_once = auto_fix_timing(code, [4.0])
        twice, applied_twice = auto_fix_timing(once, [4.0])
        assert applied_once, "first pass must scale"
        assert applied_twice == [], "second pass on corrected code must be a no-op"
        assert twice == once

    def test_already_on_target_is_no_op(self):
        # factor would be ~1.0 (within [0.98,1.02]) → no rewrite at all.
        code = textwrap.dedent("""\
            # CUE 0 — 3.0s
            self.play(Write(title), run_time=1.0)
            self.play(ShowCreation(curve), run_time=2.0)
        """)
        fixed, applied = auto_fix_timing(code, [3.0])
        assert applied == []
        assert fixed == code


class TestRunTimeScalingCannotFit:
    def test_huge_animation_tiny_d_floors_and_warns(self):
        # plays sum to 100.0, D=0.2. Even compressed, 5 literals * floor 0.1 =
        # 0.5 > 0.2 → cannot fit. Must floor each run_time, emit a warning, and
        # leave valid Python. Content is NOT dropped.
        code = textwrap.dedent("""\
            # CUE 0 — 0.2s
            self.play(a, run_time=20.0)
            self.play(b, run_time=20.0)
            self.play(c, run_time=20.0)
            self.play(d, run_time=20.0)
            self.play(e, run_time=20.0)
        """)
        fixed, applied = auto_fix_timing(code, [0.2])
        assert applied
        assert any("planner" in m.lower() or "cannot fit" in m.lower() for m in applied), (
            "an un-fittable cue must emit a planner-issue warning"
        )
        ast.parse(fixed)  # still valid Python
        lits = _runtime_literals(fixed)
        assert len(lits) == 5, "no play content dropped"
        assert all(v >= _MIN_RUN_TIME - 1e-9 for v in lits), "every run_time floored"

    def test_floor_constant_is_named(self):
        assert _MIN_RUN_TIME == pytest.approx(0.1)


class TestRunTimeScalingPreservesStructure:
    def test_cue_comments_preserved(self):
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            self.play(Write(title), run_time=1.0)

            # CUE 1 — 6.0s
            self.play(ShowCreation(curve), run_time=1.0)
        """)
        fixed, _ = auto_fix_timing(code, [4.0, 6.0])
        assert "# CUE 0" in fixed
        assert "# CUE 1" in fixed
        # Both cue blocks survive as distinct boundaries.
        assert len(_split_into_cue_blocks(fixed)) == 2

    def test_multiline_play_run_time_on_own_line_scaled(self):
        # run_time on its own line inside a multi-line self.play(...) call.
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            self.play(
                Write(title),
                ShowCreation(curve),
                run_time=2.0,
            )
        """)
        fixed, applied = auto_fix_timing(code, [4.0])
        assert applied
        lits = _runtime_literals(fixed)
        assert lits == pytest.approx([4.0])
        assert verify_timing(fixed, [4.0])["ok"]
        ast.parse(fixed)

    def test_run_time_in_comment_or_string_not_touched(self):
        # A comment AND a string literal both contain "run_time=" but are not
        # real keyword arguments — they must be left byte-for-byte intact.
        code = textwrap.dedent("""\
            # CUE 0 — 4.0s
            # note: run_time=99.0 was the old value, do not use
            label = Text("run_time=99.0")
            self.play(Write(title), run_time=2.0)
        """)
        fixed, applied = auto_fix_timing(code, [4.0])
        assert applied
        # The decoy occurrences are untouched.
        assert "run_time=99.0 was the old value" in fixed
        assert 'Text("run_time=99.0")' in fixed
        # Only the real keyword argument was scaled (2.0 → 4.0).
        lits = _runtime_literals(fixed)
        assert lits == pytest.approx([4.0])
        ast.parse(fixed)

    def test_wait_absorbs_residual_when_scaling_capped(self):
        # A wait-heavy cue: plays sum 1.0, a wait of 1.0, D=10.0. Scaling plays
        # alone (1.0→10.0 would be 10x) is allowed, but the existing behavior
        # for cues WITH a wait is preserved — verify the cue ends on target and
        # the code stays valid regardless of which lever is used.
        code = textwrap.dedent("""\
            # CUE 0 — 10.0s
            self.play(Write(title), run_time=1.0)
            self.wait(1.0)
        """)
        fixed, applied = auto_fix_timing(code, [10.0])
        assert applied
        assert verify_timing(fixed, [10.0])["ok"]
        ast.parse(fixed)
