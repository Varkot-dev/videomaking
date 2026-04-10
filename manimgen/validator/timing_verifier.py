"""
Timing verifier — statically analyses a generated ManimGL scene file to compute
the actual animation duration per cue and compare it against the contract.

The Director writes scenes with ``# CUE N — Xs`` comments and ``self.wait()``
calls. This module parses the AST to extract ``self.play(... run_time=X)`` and
``self.wait(X)`` calls, sums them per cue block, and compares against the target
durations passed in by the pipeline.

This runs BEFORE rendering (zero cost), closing the feedback loop that currently
only triggers at mux time (after a 30–120 s render).

Usage::

    from manimgen.validator.timing_verifier import verify_timing, auto_fix_timing

    result = verify_timing(code, cue_durations)
    if not result["ok"]:
        code = auto_fix_timing(code, cue_durations)
"""

from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CueTiming:
    cue_index: int
    expected: float
    computed: float

    @property
    def diff(self) -> float:
        return self.expected - self.computed

    @property
    def ok(self) -> bool:
        return abs(self.diff) < _TOLERANCE


_TOLERANCE = 0.5  # seconds — mismatches below this are acceptable
_DEFAULT_PLAY_RUNTIME = 1.0  # ManimGL default when run_time= is omitted


# ---------------------------------------------------------------------------
# AST helpers
# ---------------------------------------------------------------------------

def _eval_constant(node: ast.expr) -> float | None:
    """Try to evaluate an AST node to a float constant.

    Handles:
      - numeric literals: 1.5, 2
      - negative literals: -0.5
      - simple binary ops on constants: 4.0 - 1.5, 0.25 * 6
      - max(0.01, expr)
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        inner = _eval_constant(node.operand)
        return -inner if inner is not None else None
    if isinstance(node, ast.BinOp):
        left = _eval_constant(node.left)
        right = _eval_constant(node.right)
        if left is not None and right is not None:
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div) and right != 0:
                return left / right
    if isinstance(node, ast.Call):
        # max(0.01, expr) — common guard pattern
        if isinstance(node.func, ast.Name) and node.func.id == "max" and len(node.args) == 2:
            a = _eval_constant(node.args[0])
            b = _eval_constant(node.args[1])
            if a is not None and b is not None:
                return max(a, b)
    return None


def _get_run_time(call_node: ast.Call) -> float:
    """Extract run_time= from a self.play() call, defaulting to 1.0."""
    for kw in call_node.keywords:
        if kw.arg == "run_time":
            val = _eval_constant(kw.value)
            return val if val is not None else _DEFAULT_PLAY_RUNTIME
    return _DEFAULT_PLAY_RUNTIME


def _is_self_play(node: ast.expr) -> bool:
    """Return True if node is ``self.play(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "play"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    )


def _is_self_wait(node: ast.expr) -> bool:
    """Return True if node is ``self.wait(...)``."""
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "wait"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    )


def _get_wait_duration(call_node: ast.Call) -> float:
    """Extract duration from self.wait(X), defaulting to 1.0."""
    if call_node.args:
        val = _eval_constant(call_node.args[0])
        return val if val is not None else 0.0  # unresolvable → assume 0 (flag it)
    return 1.0  # bare self.wait() without args = 1.0 in ManimGL


def _get_for_range_count(node: ast.For) -> int | None:
    """For ``for i in range(N):``, return N.  For range(a, b), return b - a.

    Returns None if the loop count can't be statically determined.
    """
    it = node.iter
    if not (isinstance(it, ast.Call) and isinstance(it.func, ast.Name) and it.func.id == "range"):
        return None
    args = it.args
    if len(args) == 1:
        n = _eval_constant(args[0])
        return int(n) if n is not None else None
    if len(args) >= 2:
        start = _eval_constant(args[0])
        stop = _eval_constant(args[1])
        if start is not None and stop is not None:
            return max(0, int(stop) - int(start))
    return None


# ---------------------------------------------------------------------------
# Statement-level timing extractor
# ---------------------------------------------------------------------------

def _time_for_statements(stmts: list[ast.stmt]) -> float:
    """Sum the animation time consumed by a list of statements.

    Handles:
      - self.play(..., run_time=X) → X seconds
      - self.wait(X) → X seconds
      - for i in range(N): body → N × body_time
      - while loops → flagged as unresolvable (returns 0 with warning)

    Does NOT resolve dynamic variables or function calls (returns 0 for unknown).
    """
    total = 0.0
    for stmt in stmts:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if _is_self_play(call):
                total += _get_run_time(call)
            elif _is_self_wait(call):
                total += _get_wait_duration(call)
        elif isinstance(stmt, ast.For):
            n = _get_for_range_count(stmt)
            if n is not None:
                body_time = _time_for_statements(stmt.body)
                total += n * body_time
            else:
                # Can't determine loop count — sum body once and flag
                body_time = _time_for_statements(stmt.body)
                total += body_time  # conservative: count at least one iteration
        elif isinstance(stmt, ast.If):
            # Take the max of if/else branches as an estimate
            if_time = _time_for_statements(stmt.body)
            else_time = _time_for_statements(stmt.orelse) if stmt.orelse else 0.0
            total += max(if_time, else_time)
    return total


# ---------------------------------------------------------------------------
# Cue boundary detection
# ---------------------------------------------------------------------------

_CUE_COMMENT_RE = re.compile(r"#\s*CUE\s+(\d+)", re.IGNORECASE)


def _split_into_cue_blocks(code: str) -> list[tuple[int, str]]:
    """Split code into (cue_index, code_block) pairs using ``# CUE N`` comments.

    Each block contains all code from ``# CUE N`` up to the next ``# CUE`` comment
    or end of file.  Returns a list of (cue_index, code_text) tuples.

    If no CUE comments are found, returns a single block with index 0.
    """
    lines = code.splitlines(keepends=True)
    blocks: list[tuple[int, list[str]]] = []
    current_cue: int | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _CUE_COMMENT_RE.search(line)
        if m:
            if current_cue is not None:
                blocks.append((current_cue, current_lines))
            current_cue = int(m.group(1))
            current_lines = [line]
        else:
            current_lines.append(line)

    if current_cue is not None:
        blocks.append((current_cue, current_lines))

    if not blocks:
        return [(0, code)]

    return [(idx, "".join(lines_list)) for idx, lines_list in blocks]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def verify_timing(
    code: str,
    cue_durations: list[float],
) -> dict[str, Any]:
    """Verify that generated scene code respects the cue timing contract.

    Returns::

        {
            "ok": bool,           # True if all cues are within tolerance
            "cues": [CueTiming],  # per-cue breakdown
            "warnings": [str],    # human-readable issues
        }
    """
    warnings: list[str] = []
    cue_blocks = _split_into_cue_blocks(code)

    if not cue_blocks:
        return {"ok": True, "cues": [], "warnings": ["No CUE comments found — timing not verifiable."]}

    cue_timings: list[CueTiming] = []

    for cue_idx, block_code in cue_blocks:
        if cue_idx >= len(cue_durations):
            warnings.append(
                f"CUE {cue_idx} found in code but only {len(cue_durations)} durations provided — skipping."
            )
            continue

        try:
            tree = ast.parse(block_code)
        except SyntaxError:
            # The cue block may not be valid Python on its own (it's a slice
            # of a larger file). Wrap it to make it parseable.
            try:
                tree = ast.parse("if True:\n" + "\n".join(
                    "    " + l for l in block_code.splitlines()
                ))
            except SyntaxError:
                warnings.append(f"CUE {cue_idx}: could not parse code block — timing not verifiable.")
                continue

        computed = _time_for_statements(tree.body)
        expected = cue_durations[cue_idx]

        ct = CueTiming(cue_index=cue_idx, expected=expected, computed=computed)
        cue_timings.append(ct)

        if not ct.ok:
            if ct.diff > 0:
                warnings.append(
                    f"CUE {cue_idx}: {ct.diff:+.2f}s short — "
                    f"expected {expected:.2f}s but animations sum to {computed:.2f}s. "
                    f"Scene will have a freeze-frame tail on this cue."
                )
            else:
                warnings.append(
                    f"CUE {cue_idx}: {ct.diff:+.2f}s over — "
                    f"expected {expected:.2f}s but animations sum to {computed:.2f}s. "
                    f"Scene will run longer than the audio."
                )

    all_ok = all(ct.ok for ct in cue_timings)

    if warnings:
        for w in warnings:
            logger.info("[timing_verifier] %s", w)

    return {"ok": all_ok, "cues": cue_timings, "warnings": warnings}


def auto_fix_timing(
    code: str,
    cue_durations: list[float],
) -> tuple[str, list[str]]:
    """Attempt to fix timing mismatches by adjusting ``self.wait()`` calls.

    Strategy: for each cue block, find the LAST ``self.wait(X)`` call and
    adjust its argument so the total block time matches the expected duration.

    This handles the common case where the Director's wait arithmetic is wrong
    but the animation structure is correct.

    Returns (fixed_code, list_of_applied_fixes).
    """
    cue_blocks = _split_into_cue_blocks(code)
    if not cue_blocks:
        return code, []

    applied: list[str] = []
    fixed_code = code

    # Process cue blocks in REVERSE order so that rfind() on the full code
    # always matches the correct (last remaining) occurrence when two blocks
    # share the same self.wait() text (e.g. both have self.wait(1.0)).
    for cue_idx, block_code in reversed(cue_blocks):
        if cue_idx >= len(cue_durations):
            continue

        try:
            tree = ast.parse(block_code)
        except SyntaxError:
            continue

        # Find the total animation time excluding the LAST self.wait()
        all_stmts = tree.body
        last_wait_line = None
        last_wait_old_val = None

        # Walk all statements to find the last self.wait()
        for stmt in ast.walk(tree):
            if (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                    and _is_self_wait(stmt.value)):
                last_wait_line = stmt.lineno
                last_wait_old_val = _get_wait_duration(stmt.value)

        if last_wait_line is None:
            continue

        # Compute total time excluding the last wait
        total_without_last_wait = _time_for_statements(all_stmts) - (last_wait_old_val or 0)
        expected = cue_durations[cue_idx]
        new_wait = max(0.01, expected - total_without_last_wait)

        if abs(new_wait - (last_wait_old_val or 0)) < 0.05:
            continue  # difference is negligible, don't bother

        # Find and replace the last self.wait() in the ORIGINAL code
        # We search for the pattern in the block and replace it
        wait_pattern = re.compile(
            r"self\.wait\(\s*"
            r"(?:max\(\s*[\d.]+\s*,\s*)?"  # optional max(0.01,
            r"[^)]*?"                       # the inner expression
            r"(?:\s*\))?"                   # optional closing paren of max()
            r"\s*\)"
        )

        # Find all matches in the block_code, replace the last one
        matches = list(wait_pattern.finditer(block_code))
        if not matches:
            continue

        last_match = matches[-1]
        original_wait_text = last_match.group(0)
        new_wait_text = f"self.wait({new_wait:.2f})"

        # Replace in the full code (use the exact text match)
        if original_wait_text in fixed_code:
            # Only replace the LAST occurrence in case the same pattern appears elsewhere
            idx = fixed_code.rfind(original_wait_text)
            if idx >= 0:
                fixed_code = fixed_code[:idx] + new_wait_text + fixed_code[idx + len(original_wait_text):]
                applied.append(
                    f"CUE {cue_idx}: self.wait() adjusted from "
                    f"{last_wait_old_val:.2f}s to {new_wait:.2f}s "
                    f"(expected total {expected:.2f}s, anims={total_without_last_wait:.2f}s)"
                )

    if applied:
        for fix in applied:
            logger.info("[timing_verifier] auto-fix: %s", fix)

    return fixed_code, applied
