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
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class CueTiming:
    cue_index: int
    expected: float
    computed: float
    # Tri-state flag (issue #23). True when the cue's animation/wait durations
    # could not be statically resolved (e.g. run_time bound to a variable, a
    # wait() whose argument is a dynamic expression, an undeterminable loop
    # count). When the duration is UNKNOWN the ``computed`` total is a lower
    # bound only — it must NEVER be treated as an authoritative shortfall, or a
    # correct scene that uses ``run_time=rt`` gets falsely flagged as a
    # multi-second freeze and forced into a destructive retry.
    has_unknown: bool = False

    @property
    def diff(self) -> float:
        return self.expected - self.computed

    @property
    def ok(self) -> bool:
        # An UNKNOWN cue cannot be asserted mismatched — its computed total is
        # incomplete, so we conservatively treat it as acceptable rather than
        # fabricate a shortfall.
        if self.has_unknown:
            return True
        return abs(self.diff) < _TOLERANCE


_TOLERANCE = 1.0  # seconds — mismatches below this are acceptable
# Sub-1s drift (either freeze-frame tail or mild overrun) is visually unnoticeable
# once the muxer pads/trims to audio length. Retrying for sub-1s deltas was
# actively damaging already-good renders — the LLM would rewrite the scene to
# shave 0.8s and end up 3s short on a different cue.

# A cue whose animation finishes >= this many seconds BEFORE its narration
# leaves a multi-second dead/frozen screen — a real quality failure, NOT a
# brief intentional hold. Renders with such a cue must be BLOCKED (forced to
# retry), distinct from tolerable sub-1s drift. Env-tunable while we learn the
# right value empirically; default chosen to catch the +4.0/+6.65/+9.73s
# freezes observed in practice while ignoring brief holds.
_FREEZE_BLOCK_THRESHOLD = float(
    os.environ.get("MANIMGEN_FREEZE_BLOCK_THRESHOLD", "2.5")
)
_DEFAULT_PLAY_RUNTIME = 1.0  # ManimGL default when run_time= is omitted


class _Unknown:
    """Tri-state sentinel for a statically-unresolvable duration (issue #23).

    Distinct from both 0.0 and the 1.0 play-default: a duration is UNKNOWN when
    it depends on a variable or expression the static analyser cannot evaluate
    (``run_time=rt``, ``self.wait(hold)``, ``range(n)`` with dynamic ``n``).
    Silently coercing UNKNOWN to 0.0 made correct scenes look multi-seconds
    short and triggered a destructive false-freeze retry; coercing to 1.0 hid
    real shortfalls. The sentinel forces every consumer to handle the third
    state explicitly.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return "UNKNOWN"


UNKNOWN = _Unknown()

# A resolved duration is a float; an unresolved one is the UNKNOWN sentinel.
Duration = float | _Unknown


def blocking_freezes(timing_result: dict) -> list[str]:
    """Return human-readable descriptions of cues whose freeze-frame tail is
    severe enough to block render acceptance (diff >= _FREEZE_BLOCK_THRESHOLD).

    diff = expected - computed; a POSITIVE diff means the animation is shorter
    than the narration → dead screen. Overruns (negative diff) are NOT blocking
    (the muxer trims video to audio length). Sub-threshold shortfalls are NOT
    blocking — only multi-second dead screens are.

    A cue containing any UNKNOWN duration (issue #23) is NEVER blocking: its
    computed total is a lower bound, so a "shortfall" may be entirely explained
    by the unresolved dynamic duration. Blocking it would force a destructive
    retry on a scene that is, as far as we can prove, correct.
    """
    out: list[str] = []
    for ct in timing_result.get("cues", []):
        if getattr(ct, "has_unknown", False):
            continue
        if ct.diff >= _FREEZE_BLOCK_THRESHOLD:
            out.append(
                f"CUE {ct.cue_index}: {ct.diff:.2f}s frozen tail "
                f"(animation {ct.computed:.2f}s vs narration {ct.expected:.2f}s) "
                f"— exceeds {_FREEZE_BLOCK_THRESHOLD:.1f}s block threshold"
            )
    return out


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
        if (
            isinstance(node.func, ast.Name)
            and node.func.id == "max"
            and len(node.args) == 2
        ):
            a = _eval_constant(node.args[0])
            b = _eval_constant(node.args[1])
            if a is not None and b is not None:
                return max(a, b)
    return None


def _get_run_time(call_node: ast.Call) -> Duration:
    """Extract run_time= from a self.play() call.

    Returns the ManimGL default (1.0) when ``run_time=`` is omitted, the
    constant value when it can be statically evaluated, and UNKNOWN when the
    argument is a dynamic expression (issue #23 — no longer silently treated as
    the 1.0 default, which masked real timing and produced false freezes).
    """
    for kw in call_node.keywords:
        if kw.arg == "run_time":
            val = _eval_constant(kw.value)
            return val if val is not None else UNKNOWN
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


def _get_wait_duration(call_node: ast.Call) -> Duration:
    """Extract duration from self.wait(X).

    A bare ``self.wait()`` is 1.0 in ManimGL. A constant argument resolves to
    its value. A dynamic argument (e.g. ``self.wait(hold)``) is UNKNOWN — NOT
    0.0 (issue #23). The old 0.0 default was the asymmetric bug: a scene that
    intentionally held for a computed duration was scored as a multi-second
    shortfall and force-retried into a broken state.
    """
    if call_node.args:
        val = _eval_constant(call_node.args[0])
        return val if val is not None else UNKNOWN
    return 1.0  # bare self.wait() without args = 1.0 in ManimGL


def _get_for_range_count(node: ast.For) -> int | None:
    """For ``for i in range(N):``, return N.  For range(a, b), return b - a.

    Returns None if the loop count can't be statically determined.
    """
    it = node.iter
    if not (
        isinstance(it, ast.Call)
        and isinstance(it.func, ast.Name)
        and it.func.id == "range"
    ):
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


def _time_for_statements(stmts: list[ast.stmt]) -> tuple[float, bool]:
    """Sum the animation time consumed by a list of statements (issue #23).

    Returns ``(total_seconds, has_unknown)``. ``total_seconds`` is the sum of
    every *resolvable* duration. ``has_unknown`` is True when any duration could
    not be statically determined, in which case ``total_seconds`` is only a
    lower bound and must not be treated as authoritative.

    Handles:
      - self.play(..., run_time=X) → X seconds (UNKNOWN if run_time is dynamic)
      - self.wait(X) → X seconds (UNKNOWN if X is dynamic)
      - for i in range(N): body → N × body_time (UNKNOWN if N is undeterminable)
      - if/else → max of the two branches
    """
    total = 0.0
    has_unknown = False
    for stmt in stmts:
        if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
            call = stmt.value
            if _is_self_play(call):
                rt = _get_run_time(call)
                if isinstance(rt, _Unknown):
                    has_unknown = True
                else:
                    total += rt
            elif _is_self_wait(call):
                wt = _get_wait_duration(call)
                if isinstance(wt, _Unknown):
                    has_unknown = True
                else:
                    total += wt
        elif isinstance(stmt, ast.For):
            n = _get_for_range_count(stmt)
            body_time, body_unknown = _time_for_statements(stmt.body)
            if body_unknown:
                has_unknown = True
            if n is not None:
                total += n * body_time
            else:
                # Can't determine loop count — the per-iteration time is known
                # but the iteration count is not, so the loop's contribution is
                # unresolvable. Count one iteration as a lower bound and flag.
                total += body_time
                has_unknown = True
        elif isinstance(stmt, ast.If):
            # Take the max of if/else branches as an estimate.
            if_time, if_unknown = _time_for_statements(stmt.body)
            else_time, else_unknown = (
                _time_for_statements(stmt.orelse) if stmt.orelse else (0.0, False)
            )
            total += max(if_time, else_time)
            if if_unknown or else_unknown:
                has_unknown = True
    return total, has_unknown


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
        return {
            "ok": True,
            "cues": [],
            "warnings": ["No CUE comments found — timing not verifiable."],
        }

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
                tree = ast.parse(
                    "if True:\n"
                    + "\n".join("    " + l for l in block_code.splitlines())
                )
            except SyntaxError:
                warnings.append(
                    f"CUE {cue_idx}: could not parse code block — timing not verifiable."
                )
                continue

        computed, has_unknown = _time_for_statements(tree.body)
        expected = cue_durations[cue_idx]

        ct = CueTiming(
            cue_index=cue_idx,
            expected=expected,
            computed=computed,
            has_unknown=has_unknown,
        )
        cue_timings.append(ct)

        if has_unknown:
            # Do NOT assert a shortfall/overrun: the computed total is a lower
            # bound only (a dynamic run_time/wait/loop count was present). A
            # bare informational note keeps the LLM aware without triggering an
            # auto-fix or a false-freeze block (issue #23).
            warnings.append(
                f"CUE {cue_idx}: contains a dynamic duration "
                f"(run_time/wait/loop count not statically resolvable) — "
                f"timing not verifiable, skipping freeze check for this cue."
            )
        elif not ct.ok:
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

    # Warnings are returned to the caller — do NOT log here. The retry loop
    # prints them once via its own "[retry]" prefix, and logging them here
    # caused 3x duplication for every single warning (once from cli first-pass,
    # once from retry pre-pass, once from retry re-verify).

    return {"ok": all_ok, "cues": cue_timings, "warnings": warnings}


def _strip_phantom_cue_blocks(
    code: str,
    max_valid_index: int,
) -> tuple[str, list[str]]:
    """Delete CUE blocks whose index is >= max_valid_index.

    The Director sometimes hallucinates a CUE N comment when only N durations
    were provided. The extra cue block's animations push the scene past the
    audio duration and every retry regenerates the same phantom, burning the
    retry budget. Physically removing those blocks from the source before
    rendering prevents the cycle.

    A phantom block runs from its ``# CUE N`` line up to the next ``# CUE`` or
    the end of the construct method (whichever comes first). The final
    ``self.play(*[FadeOut...]`` cleanup is preserved if it is outside any CUE
    block, but if it lives inside the phantom block it is sacrificed — the
    previous (valid) cue's FadeOut at end-of-section covers the cleanup.
    """
    lines = code.splitlines(keepends=True)
    cue_re = _CUE_COMMENT_RE
    out_lines: list[str] = []
    stripped_lines: list[str] = []  # everything we removed — scan for cleanup
    stripping = False
    removed: list[str] = []

    for line in lines:
        m = cue_re.search(line)
        if m:
            cue_idx = int(m.group(1))
            if cue_idx >= max_valid_index:
                stripping = True
                removed.append(f"CUE {cue_idx}")
                stripped_lines.append(line)
                continue
            stripping = False
            out_lines.append(line)
            continue
        if stripping:
            # Lines that are not indented at all belong to the enclosing module,
            # not the cue block — stop stripping at them.
            stripped_prefix = line.lstrip()
            if stripped_prefix and not line.startswith((" ", "\t")):
                stripping = False
                out_lines.append(line)
                continue
            stripped_lines.append(line)
            continue
        out_lines.append(line)

    if not removed:
        return code, []

    # Preserve any final FadeOut-everything cleanup that was inside the phantom
    # block — otherwise the scene ends on a static frame instead of fading out.
    cleanup_re = re.compile(r"self\.play\s*\(\s*\*\s*\[\s*FadeOut")
    cleanup_line = next(
        (ln for ln in stripped_lines if cleanup_re.search(ln)),
        None,
    )
    if cleanup_line:
        out_lines.append(cleanup_line)

    note = [
        f"Stripped phantom {label} (only {max_valid_index} durations provided)"
        for label in removed
    ]
    return "".join(out_lines), note


def auto_fix_timing(
    code: str,
    cue_durations: list[float],
) -> tuple[str, list[str]]:
    """Attempt to fix timing mismatches by adjusting ``self.wait()`` calls.

    Strategy: for each cue block, find the LAST ``self.wait(X)`` call and
    adjust its argument so the total block time matches the expected duration.

    This handles the common case where the Director's wait arithmetic is wrong
    but the animation structure is correct.

    Before the wait-adjustment pass, any CUE block with index >= len(cue_durations)
    is physically deleted. Those phantom blocks are the Section-2-style failure
    mode where every retry regenerates the same invalid cue and burns the budget.

    Returns (fixed_code, list_of_applied_fixes).
    """
    applied: list[str] = []
    fixed_code, phantom_fixes = _strip_phantom_cue_blocks(code, len(cue_durations))
    applied.extend(phantom_fixes)

    cue_blocks = _split_into_cue_blocks(fixed_code)
    if not cue_blocks:
        return fixed_code, applied

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
        last_wait_old_val: Duration | None = None

        # Walk all statements to find the last self.wait()
        for stmt in ast.walk(tree):
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and _is_self_wait(stmt.value)
            ):
                last_wait_line = stmt.lineno
                last_wait_old_val = _get_wait_duration(stmt.value)

        if last_wait_line is None:
            continue

        # If the LAST wait's duration is itself dynamic (UNKNOWN), we cannot
        # compute a safe replacement — leave it untouched (issue #23). Rewriting
        # a deliberate dynamic hold to a fabricated constant is exactly the
        # destructive behaviour we are eliminating.
        if isinstance(last_wait_old_val, _Unknown):
            continue

        # Compute total time excluding the last wait.
        full_total, block_unknown = _time_for_statements(all_stmts)
        total_without_last_wait = full_total - (last_wait_old_val or 0)

        # If any OTHER duration in the block is dynamic, the residual is
        # unknowable — adjusting the wait would over- or under-shoot. Skip.
        if block_unknown:
            continue
        expected = cue_durations[cue_idx]
        new_wait = max(0.01, expected - total_without_last_wait)

        if abs(new_wait - (last_wait_old_val or 0)) < 0.05:
            continue  # difference is negligible, don't bother

        # Find and replace the last self.wait() in the ORIGINAL code
        # We search for the pattern in the block and replace it
        wait_pattern = re.compile(
            r"self\.wait\(\s*"
            r"(?:max\(\s*[\d.]+\s*,\s*)?"  # optional max(0.01,
            r"[^)]*?"  # the inner expression
            r"(?:\s*\))?"  # optional closing paren of max()
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
                fixed_code = (
                    fixed_code[:idx]
                    + new_wait_text
                    + fixed_code[idx + len(original_wait_text) :]
                )
                applied.append(
                    f"CUE {cue_idx}: self.wait() adjusted from "
                    f"{last_wait_old_val:.2f}s to {new_wait:.2f}s "
                    f"(expected total {expected:.2f}s, anims={total_without_last_wait:.2f}s)"
                )

    if applied:
        for fix in applied:
            logger.info("[timing_verifier] auto-fix: %s", fix)

    return fixed_code, applied
