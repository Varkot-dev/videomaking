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

from manimgen.validator.frame_checker import FROZEN_FRAME_MARKER
from manimgen.validator.manimlib_signatures import _split_source_lines

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

# Compression floor (issue #59). A scaled-down run_time is never written below
# this — an animation faster than ~0.1s reads as an instant cut, not motion. If
# even at the floor a cue's animations cannot be squeezed into its narration D,
# we render best-effort at the floor and emit a planner-issue warning rather
# than mangling or dropping content.
_MIN_RUN_TIME = 0.1

# Scaling is treated as a no-op when the required factor is this close to 1.0.
# Guards idempotency: after verify→fix→re-verify, a second auto_fix pass on the
# already-corrected code sees factor≈1.0 and rewrites nothing (no double-scale).
_SCALE_NOOP_LO = 0.98
_SCALE_NOOP_HI = 1.02

# Largest stretch we will apply by slowing animations. Beyond this a play would
# read as unnaturally slow (a 2s Write dragged to 10s looks broken), so the gap
# is better absorbed by HOLDING the final frame with a self.wait() — the issue
# #22 deterministic residual resolver. This is constraint 7's "only adjust the
# wait when scaling plays alone cannot [reasonably] absorb the gap". Compression
# (factor < 1) has no such cap — it is always preferable to a frame the cutter
# would otherwise drop as an over-length clip.
_MAX_STRETCH_FACTOR = 3.0


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


_FROZEN_FRAME_MARKER = FROZEN_FRAME_MARKER


def join_frozen_with_timing(
    frame_issues: list[str],
    timing_freeze_confirmed: bool,
) -> list[str]:
    """Join frame_checker's frozen-frame signal with the timing oracle (#32).

    frame_checker emits a ``"... identical — animation appears frozen"`` line
    whenever two sampled frames match. On its own that signal cannot tell a
    legit narration HOLD (narrator still talking over a deliberately static
    visual) from a dead tail (animation ended early, multi-second frozen
    screen). Making it hard unconditionally false-positives on every legit
    final hold — which is exactly why it was being discarded outright.

    The timing_verifier QUANTIFIES the difference: ``blocking_freezes`` is
    non-empty only when a cue's resolvable animation ends >= the block
    threshold before its narration (a real dead tail), and is empty for legit
    holds and for UNKNOWN cues. So a frozen frame is admitted as a HARD issue
    IFF timing independently confirms a dead tail. Otherwise it is dropped as a
    legit hold.

    Non-frozen frame issues (black frames, edge clipping) always pass through
    unchanged — they are not subject to the timing join.

    Returns the filtered list of frame issues to fold into ``combined_issues``.
    """
    kept: list[str] = []
    for issue in frame_issues:
        if _FROZEN_FRAME_MARKER in issue:
            if timing_freeze_confirmed:
                kept.append(issue)
            # else: legit hold — drop the frozen signal, do not false-positive.
            continue
        kept.append(issue)
    return kept


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
    # No play-level run_time. An animation carrying its own run_time
    # (self.play(Write(m, run_time=2.0))) runs for that long, not the 1.0s
    # default, and several of them combine by max. Not modelled: UNKNOWN.
    for arg in call_node.args:
        for sub_node in ast.walk(arg):
            if isinstance(sub_node, ast.Call) and any(
                kw.arg == "run_time" for kw in sub_node.keywords
            ):
                return UNKNOWN
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
    """Iteration count of ``for i in range(...)`` with constant integer args.

    Honours start and step, so ``range(5, 0, -1)`` is 5 and ``range(0, 10, 2)``
    is 5. Returns None if the count can't be statically determined.
    """
    it = node.iter
    if not (
        isinstance(it, ast.Call)
        and isinstance(it.func, ast.Name)
        and it.func.id == "range"
        and not it.keywords
        and 1 <= len(it.args) <= 3
    ):
        return None
    values: list[int] = []
    for arg in it.args:
        val = _eval_constant(arg)
        if val is None or val != int(val):
            return None
        values.append(int(val))
    try:
        return len(range(*values))
    except ValueError:  # range() step of zero
        return None


# Time spent by ShortScene methods (manimgen/scenekit/shorts.py): each plays
# for a fixed time the static analysis cannot see inside the method.
# name -> (fixed seconds, default run_time of the part set by run_time=).
_KIT_TIMED_CALLS = {
    "swap_hook": (0.25, 0.5),  # old text out, then new text in
    "swap_key": (0.25, 0.5),
    "drop": (0.0, 0.25),  # fade out and forget
}


def _kit_call_time(call: ast.Call) -> Duration | None:
    """Seconds a ShortScene timed method takes, or None if ``call`` is not one."""
    func = call.func
    if not (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "self"
        and func.attr in _KIT_TIMED_CALLS
    ):
        return None
    fixed, default = _KIT_TIMED_CALLS[func.attr]
    for kw in call.keywords:
        if kw.arg == "run_time":
            val = _eval_constant(kw.value)
            return fixed + val if val is not None else UNKNOWN
    return fixed + default


def _is_timed_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and (
        _is_self_play(node) or _is_self_wait(node) or _kit_call_time(node) is not None
    )


def _contains_timed_call(stmts: list[ast.stmt]) -> bool:
    return any(_is_timed_call(n) for stmt in stmts for n in ast.walk(stmt))


def _timed_helper_names(code: str) -> frozenset[str]:
    """Names of methods (other than construct) that call self.play/self.wait.

    A cue that calls ``self.<helper>()`` spends time the block-level analysis
    cannot see, so such a cue is treated as UNKNOWN. Empty when ``code`` does
    not parse.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return frozenset()
    return frozenset(
        node.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name != "construct"
        and _contains_timed_call(node.body)
    )


def _calls_helper(stmt: ast.stmt, helpers: frozenset[str]) -> bool:
    if not helpers:
        return False
    for node in ast.walk(stmt):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in helpers
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            return True
    return False


# ---------------------------------------------------------------------------
# Statement-level timing extractor
# ---------------------------------------------------------------------------


def _time_for_statements(
    stmts: list[ast.stmt],
    helpers: frozenset[str] = frozenset(),
) -> tuple[float, bool]:
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
      - with: body counted once
      - while / try / match containing a play or wait → UNKNOWN
      - a call to ``self.<name>()`` where ``name`` is in ``helpers`` → UNKNOWN
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
                continue
            if _is_self_wait(call):
                wt = _get_wait_duration(call)
                if isinstance(wt, _Unknown):
                    has_unknown = True
                else:
                    total += wt
                continue
            kt = _kit_call_time(call)
            if kt is not None:
                if isinstance(kt, _Unknown):
                    has_unknown = True
                else:
                    total += kt
                continue
        if isinstance(stmt, ast.Assign) and isinstance(stmt.value, ast.Call):
            kt = _kit_call_time(stmt.value)
            if kt is not None:
                if isinstance(kt, _Unknown):
                    has_unknown = True
                else:
                    total += kt
                continue
        if isinstance(stmt, ast.For):
            n = _get_for_range_count(stmt)
            body_time, body_unknown = _time_for_statements(stmt.body, helpers)
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
            if _contains_timed_call(stmt.orelse):
                has_unknown = True
        elif isinstance(stmt, ast.If):
            # Take the max of if/else branches as an estimate.
            if_time, if_unknown = _time_for_statements(stmt.body, helpers)
            else_time, else_unknown = (
                _time_for_statements(stmt.orelse, helpers)
                if stmt.orelse
                else (0.0, False)
            )
            total += max(if_time, else_time)
            if if_unknown or else_unknown:
                has_unknown = True
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            body_time, body_unknown = _time_for_statements(stmt.body, helpers)
            total += body_time
            has_unknown = has_unknown or body_unknown
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue  # a definition spends no time where it is defined
        elif isinstance(stmt, (ast.While, ast.Try, ast.Match)) or (
            hasattr(ast, "TryStar") and isinstance(stmt, ast.TryStar)
        ):
            # Iteration count / taken path is not knowable statically.
            if _contains_timed_call([stmt]) or _calls_helper(stmt, helpers):
                has_unknown = True
        elif _calls_helper(stmt, helpers):
            has_unknown = True
    return total, has_unknown


# ---------------------------------------------------------------------------
# Cue boundary detection
# ---------------------------------------------------------------------------

# A cue marker is a comment naming the cue: "# CUE 3", and also the decorated
# forms models write, "# ---------------- CUE 3 — 2.19s ----------------" or
# "# === CUE 3 ===". Only punctuation and spaces may sit between "#" and "CUE",
# so prose such as "# see CUE 3 above" is not a marker.
_CUE_COMMENT_RE = re.compile(r"#[^\w\n]*CUE\s+(\d+)", re.IGNORECASE)


def _has_cue_markers(code: str) -> bool:
    return bool(_CUE_COMMENT_RE.search(code))


def _function_line_ranges(code: str) -> list[tuple[int, int]]:
    """(first_line, last_line), 1-based, of every function in ``code``.

    Empty when ``code`` is not a parseable module (e.g. an indented fragment).
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []
    return [
        (node.lineno, node.end_lineno or node.lineno)
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _enclosing_function_end(ranges: list[tuple[int, int]], lineno: int) -> int | None:
    """Last line of the innermost function containing ``lineno``, if any."""
    best: tuple[int, int] | None = None
    for start, end in ranges:
        if start <= lineno <= end and (best is None or start >= best[0]):
            best = (start, end)
    return best[1] if best else None


def _construct_body_source(code: str) -> str | None:
    """Source lines of the body of ``construct``, or None if there is none."""
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return None
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "construct"
            and node.body
        ):
            lines = code.splitlines(keepends=True)
            first = node.body[0].lineno
            last = node.end_lineno or first
            return "".join(lines[first - 1 : last])
    return None


def _split_into_cue_blocks(code: str) -> list[tuple[int, str]]:
    """Split code into (cue_index, code_block) pairs using ``# CUE N`` comments.

    Each block contains all code from ``# CUE N`` up to the next ``# CUE``
    comment, the end of the function the comment sits in (so helper methods
    defined after ``construct`` are not swallowed by the last cue), or the end
    of the file. Returns a list of (cue_index, code_text) tuples.

    If no CUE comments are found, returns a single block with index 0.
    """
    lines = code.splitlines(keepends=True)
    ranges = _function_line_ranges(code)
    blocks: list[tuple[int, list[str]]] = []
    current_cue: int | None = None
    current_lines: list[str] = []
    current_end: int | None = None

    for lineno, line in enumerate(lines, start=1):
        m = _CUE_COMMENT_RE.search(line)
        if m:
            if current_cue is not None:
                blocks.append((current_cue, current_lines))
            current_cue = int(m.group(1))
            current_lines = [line]
            current_end = _enclosing_function_end(ranges, lineno)
        elif current_cue is not None:
            if current_end is not None and lineno > current_end:
                blocks.append((current_cue, current_lines))
                current_cue = None
                current_lines = []
            else:
                current_lines.append(line)

    if current_cue is not None:
        blocks.append((current_cue, current_lines))

    if not blocks:
        # No markers: the only sensible single block is the body of construct()
        # (a one-cue section). Returning the whole file would time the module's
        # top level, which is just an import and a class.
        body = _construct_body_source(code)
        return [(0, body if body is not None else code)]

    return [(idx, "".join(lines_list)) for idx, lines_list in blocks]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _parse_cue_block(block_code: str) -> ast.Module | None:
    """Parse a cue-block slice into an AST.

    A cue block is a slice of a larger file, so it may be indented (it lives
    inside ``construct``). A bare ``ast.parse`` raises IndentationError on such
    a slice; wrapping it in ``if True:`` makes it parseable while preserving the
    statement structure (``_time_for_statements`` treats the wrapping If as a
    single-branch block of identical duration). Returns None if it still won't
    parse.
    """
    try:
        return ast.parse(block_code)
    except (SyntaxError, IndentationError):
        try:
            return ast.parse(
                "if True:\n"
                + "\n".join("    " + line for line in block_code.splitlines())
            )
        except (SyntaxError, IndentationError):
            return None


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
    if not _has_cue_markers(code) and len(cue_durations) != 1:
        # Without markers nothing says which statements belong to which cue.
        # Scoring the whole file against the first cue's duration produced
        # nonsense shortfalls and "fixes" that broke the scene.
        return {
            "ok": True,
            "cues": [],
            "warnings": ["No CUE comments found — timing not verifiable."],
        }
    cue_blocks = _split_into_cue_blocks(code)
    helpers = _timed_helper_names(code)

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

        tree = _parse_cue_block(block_code)
        if tree is None:
            warnings.append(
                f"CUE {cue_idx}: could not parse code block — timing not verifiable."
            )
            continue

        computed, has_unknown = _time_for_statements(tree.body, helpers)
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
    ranges = _function_line_ranges(code)
    out_lines: list[str] = []
    stripped_lines: list[str] = []  # everything we removed — scan for cleanup
    stripping = False
    strip_end: int | None = None
    strip_cut: int | None = None  # index in out_lines where the last phantom sat
    removed: list[str] = []

    for lineno, line in enumerate(lines, start=1):
        m = cue_re.search(line)
        if m:
            cue_idx = int(m.group(1))
            if cue_idx >= max_valid_index:
                stripping = True
                # Never strip past the function holding the phantom cue: a
                # helper method defined after construct() is still called by
                # the valid cues.
                strip_end = _enclosing_function_end(ranges, lineno)
                strip_cut = len(out_lines)
                removed.append(f"CUE {cue_idx}")
                stripped_lines.append(line)
                continue
            stripping = False
            out_lines.append(line)
            continue
        if stripping and strip_end is not None and lineno > strip_end:
            stripping = False
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
        # Put the cleanup where the phantom block was, not at the end of the
        # file (which may now be a helper method).
        insert_at = len(out_lines)
        if strip_cut is not None:
            insert_at = strip_cut
        out_lines.insert(insert_at, cleanup_line)

    note = [
        f"Stripped phantom {label} (only {max_valid_index} durations provided)"
        for label in removed
    ]
    return "".join(out_lines), note


_MIN_INSERT_RESIDUAL = _TOLERANCE
# Only insert a missing wait when the shortfall exceeds the tolerance band.
# Below tolerance the muxer's pad/trim already covers the drift and inserting a
# sub-second wait would be churn for no visible benefit.


def _parse_block_with_shift(
    block_code: str,
) -> tuple[ast.Module, list[ast.stmt], int, int] | None:
    """Parse a cue block and return ``(tree, top_level_stmts, line_shift, col_shift)``.

    An indented block is parsed inside an ``if True:`` wrapper (see
    ``_parse_cue_block``); the shifts translate AST positions back to
    ``block_code`` coordinates and ``top_level_stmts`` are the cue's own
    statements, not the wrapper.
    """
    try:
        tree = ast.parse(block_code)
        return tree, tree.body, 0, 0
    except (SyntaxError, IndentationError, ValueError):
        pass
    try:
        tree = ast.parse(
            "if True:\n" + "\n".join("    " + line for line in block_code.splitlines())
        )
    except (SyntaxError, IndentationError, ValueError):
        return None
    wrapper = tree.body[0] if tree.body else None
    stmts = wrapper.body if isinstance(wrapper, ast.If) else tree.body
    return tree, stmts, 1, 4


def _block_offset(block_code: str, lineno: int, byte_col: int) -> int | None:
    """Character offset in ``block_code`` of a 1-based line and a UTF-8 BYTE
    column (what ``ast`` reports). Treating the byte column as a character
    column misplaces every edit on a line that has non-ASCII text before it.
    """
    lines = _split_source_lines(block_code)
    if lineno < 1 or lineno > len(lines) or byte_col < 0:
        return None
    line = lines[lineno - 1]
    char_col = len(line.encode("utf-8")[:byte_col].decode("utf-8", errors="ignore"))
    return sum(len(ln) for ln in lines[: lineno - 1]) + char_col


def _node_span(
    block_code: str, node: ast.AST, line_shift: int, col_shift: int
) -> tuple[int, int] | None:
    """(start, end) character offsets of ``node`` within ``block_code``."""
    end_lineno = getattr(node, "end_lineno", None)
    end_col = getattr(node, "end_col_offset", None)
    if end_lineno is None or end_col is None:
        return None
    start = _block_offset(
        block_code, node.lineno - line_shift, node.col_offset - col_shift
    )
    end = _block_offset(block_code, end_lineno - line_shift, end_col - col_shift)
    if start is None or end is None or end < start:
        return None
    return start, end


def _last_meaningful_line(block_code: str) -> tuple[str, str] | None:
    """Return (line_text_without_newline, indent) of the last code line in a
    cue block — the line a synthetic wait should be inserted *after*.

    Skips trailing blank lines and pure-comment lines. Returns None when the
    block has no code line to anchor to.
    """
    for raw in reversed(block_code.splitlines()):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = raw[: len(raw) - len(raw.lstrip())]
        return raw, indent
    return None


def _insert_missing_wait(
    fixed_code: str,
    block_code: str,
    cue_idx: int,
    residual: float,
) -> tuple[str, str | None]:
    """Insert ``self.wait(residual)`` at the end of a cue block that has NO
    ``self.wait()`` at all (issue #22 — the deterministic residual resolver).

    ``auto_fix_timing`` previously no-op'd on these blocks, leaving the exact
    computed shortfall as a freeze-frame tail and handing the problem to the
    LLM as an advisory hint. The verifier already knows the precise residual;
    this makes it authoritative by inserting the wait at the correct boundary.

    Anchors within the cue BLOCK, not the whole file: first locate the block
    (its ``# CUE N`` header makes ``block_code`` unique), then find the anchor
    line inside that block's span. Searching the whole file with rfind would
    land the wait in the wrong cue when two cues share byte-identical play text.
    Returns ``(new_code, applied_message_or_None)``.
    """
    block_idx = fixed_code.rfind(block_code)
    if block_idx < 0:
        return fixed_code, None

    # Anchor after the last TOP-LEVEL statement of the cue, at that statement's
    # indent. Anchoring on the last text line put the wait inside a loop body
    # when the cue ended with a loop, multiplying it by the iteration count.
    insert_in_block: int | None = None
    indent = ""
    parsed = _parse_block_with_shift(block_code)
    if parsed is not None and parsed[1]:
        _tree, stmts, line_shift, _col_shift = parsed
        last = stmts[-1]
        if isinstance(
            last,
            (
                ast.ClassDef,
                ast.FunctionDef,
                ast.AsyncFunctionDef,
                ast.Import,
                ast.ImportFrom,
            ),
        ):
            # The block is not a cue body (for example a whole file whose last
            # statement is the scene class). A wait placed after it would sit
            # outside construct(), where the safety gate rejects the file.
            return fixed_code, None
        block_lines = _split_source_lines(block_code)
        first_line = last.lineno - line_shift
        end_line = (last.end_lineno or last.lineno) - line_shift
        if 1 <= first_line <= end_line <= len(block_lines):
            first_text = block_lines[first_line - 1]
            indent = first_text[: len(first_text) - len(first_text.lstrip())]
            insert_in_block = sum(len(ln) for ln in block_lines[:end_line])
            # Insert before the line's trailing newline, if any.
            end_text = block_lines[end_line - 1]
            insert_in_block -= len(end_text) - len(end_text.rstrip("\r\n"))
    if insert_in_block is None:
        anchor = _last_meaningful_line(block_code)
        if anchor is None:
            return fixed_code, None
        anchor_line, indent = anchor
        anchor_in_block = block_code.rfind(anchor_line)
        if anchor_in_block < 0:
            return fixed_code, None
        insert_in_block = anchor_in_block + len(anchor_line)
    insert_at = block_idx + insert_in_block

    wait_line = f"\n{indent}self.wait({residual:.2f})  # CUE_FILL auto-inserted"
    new_code = fixed_code[:insert_at] + wait_line + fixed_code[insert_at:]
    msg = (
        f"CUE {cue_idx}: inserted self.wait({residual:.2f}) at cue boundary "
        f"(no existing wait; closing a {residual:.2f}s freeze-frame tail)"
    )
    return new_code, msg


# ---------------------------------------------------------------------------
# run_time scaling (issue #59)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _RunTimeLiteral:
    """A resolvable ``run_time=`` constant located within a cue block.

    Offsets are character indices into the *block* string (already translated
    out of any ``if True:`` parse-wrapper), so the literal can be sliced and
    replaced without re-tokenising. ``value`` is the current float.
    """

    start: int
    end: int
    value: float


def _block_line_starts(block_code: str) -> list[int]:
    """Character offset of the first char of each 1-based line in block_code."""
    starts = [0]
    for line in block_code.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    return starts


def _collect_play_run_time_consts(
    block_code: str,
) -> tuple[list[_RunTimeLiteral], bool]:
    """Locate every CONSTANT ``run_time=`` literal inside ``self.play(...)``.

    Returns ``(literals, parse_ok)``. Each literal carries its character span
    within ``block_code`` so callers can do precise, offset-based replacement —
    NEVER regex/rfind, which mis-handled loops and multi-line calls (#55/#56/
    #57). The full subtree is walked (``ast.walk``), so occurrences inside
    ``for`` loops and ``if`` branches are all captured, not just the last.

    A dynamic ``run_time=expr`` contributes no literal (it is UNKNOWN and must
    not be scaled). ``parse_ok`` is False when the block could not be parsed.
    """
    parsed = _parse_block_with_shift(block_code)
    if parsed is None:
        return [], False
    tree, _stmts, line_shift, col_shift = parsed
    literals: list[_RunTimeLiteral] = []

    for node in ast.walk(tree):
        if not _is_self_play(node):
            continue
        for kw in node.keywords:
            if kw.arg != "run_time":
                continue
            val = _eval_constant(kw.value)
            if val is None:
                continue  # dynamic — UNKNOWN, never scale
            span = _node_span(block_code, kw.value, line_shift, col_shift)
            if span is None:
                continue
            literals.append(_RunTimeLiteral(start=span[0], end=span[1], value=val))

    # Sort by position so replacement (done in reverse) stays offset-stable.
    literals.sort(key=lambda lit: lit.start)
    return literals, True


def _format_run_time(value: float) -> str:
    """Render a scaled run_time as a clean 3-decimal float literal.

    ``round(.., 3)`` then ``repr`` strips trailing-zero/float-noise artefacts
    (0.156, not 0.15600000001 and not 0.1560).
    """
    return repr(round(value, 3))


def _scale_run_times_in_block(
    block_code: str,
    literals: list[_RunTimeLiteral],
    factor: float,
) -> tuple[str, bool]:
    """Rewrite every located run_time literal by ``factor``, clamped to the
    compression floor. Returns ``(new_block, hit_floor)``.

    Replacements are applied from the highest offset down so each edit leaves
    the offsets of the not-yet-applied (earlier) literals valid.
    """
    hit_floor = False
    new_block = block_code
    for lit in sorted(literals, key=lambda l: l.start, reverse=True):
        scaled = lit.value * factor
        if scaled < _MIN_RUN_TIME:
            scaled = _MIN_RUN_TIME
            hit_floor = True
        new_block = (
            new_block[: lit.start] + _format_run_time(scaled) + new_block[lit.end :]
        )
    return new_block, hit_floor


def _try_scale_plays(
    fixed_code: str,
    block_code: str,
    cue_idx: int,
    all_stmts: list[ast.stmt],
    expected: float,
    helpers: frozenset[str] = frozenset(),
) -> tuple[str, str | None]:
    """Scale a cue's self.play(run_time=...) literals so its animations fill the
    narration duration ``expected`` (issue #59).

    Applied only to cue blocks with NO adjustable trailing self.wait() — the
    exact failure mode that produced 262-byte empty cue videos: a continuous
    animation far shorter than its narration with no wait to stretch. Cues that
    DO have a trailing wait keep the existing wait-adjustment path (the wait
    deterministically absorbs the whole gap there).

    Returns ``(new_code, applied_message_or_None)``. A None message means "not
    applicable / no-op" — the caller then falls through to the wait logic.

    Guards (recreating any of these reintroduces a known bug):
      - block_unknown → never scale (dynamic run_time/loop count, #23).
      - play_total == 0 (zero-play cue) → never divide by zero; caller's wait
        path fills D instead.
      - factor within the no-op band → leave the code byte-for-byte (idempotency).
      - un-fittable even at the floor → floor every literal + emit a
        planner-issue warning; never drop or mangle content.
    """
    block_total, block_unknown = _time_for_statements(all_stmts, helpers)
    if block_unknown:
        return fixed_code, None  # UNKNOWN — fall back to wait path / no-op

    literals, parse_ok = _collect_play_run_time_consts(block_code)
    if not parse_ok or not literals:
        return fixed_code, None

    # Loop-aware play total: each literal already weighted by its loop count is
    # exactly ``block_total`` minus the resolved waits. We need only the play
    # contribution, so recompute it directly from the statement walk minus waits
    # is unnecessary — for the no-wait blocks this path handles, block_total IS
    # the play total. Scaling every literal by ``factor`` scales an in-loop
    # literal's N×contribution by the same factor, so per-literal scaling is
    # loop-correct.
    play_total = block_total
    if play_total <= 0:
        return fixed_code, None  # zero-play — caller fills with a wait

    if abs(expected - play_total) < _TOLERANCE:
        # Within the tolerance band the muxer's pad/trim already covers the
        # drift; rewriting run_times here would be churn for no visible benefit
        # (and would fight idempotency on sub-second deltas). Strict < matches
        # CueTiming.ok so an exactly-1.0s shortfall is fixed, not no-op'd.
        return fixed_code, None

    # Only the literal run_times can be rewritten; a play with no run_time=
    # keeps its 1.0s default. Measure the scalable share by re-timing the block
    # with every literal doubled (loop-aware: total = fixed + scalable), then
    # scale that share alone so the cue lands on ``expected``.
    doubled_block, _ = _scale_run_times_in_block(block_code, literals, 2.0)
    doubled_tree = _parse_cue_block(doubled_block)
    if doubled_tree is None:
        return fixed_code, None
    doubled_total, _ = _time_for_statements(doubled_tree.body, helpers)
    scalable = doubled_total - play_total
    fixed_share = play_total - scalable
    if scalable <= 1e-9 or expected - fixed_share <= 0:
        return fixed_code, None  # nothing scalable can close the gap

    factor = (expected - fixed_share) / scalable
    if _SCALE_NOOP_LO <= factor <= _SCALE_NOOP_HI:
        return fixed_code, None  # already on target — idempotent no-op
    if factor > _MAX_STRETCH_FACTOR:
        # Too large a stretch to absorb by slowing animations — hand back to the
        # caller's wait-insertion path, which holds the final frame to fill D.
        return fixed_code, None

    new_block, hit_floor = _scale_run_times_in_block(block_code, literals, factor)

    # Locate this block in the full code (reverse-order processing + rfind keeps
    # the match correct even when two blocks share identical text).
    idx = fixed_code.rfind(block_code)
    if idx < 0:
        return fixed_code, None
    new_code = fixed_code[:idx] + new_block + fixed_code[idx + len(block_code) :]

    # Determine, loop-aware, the post-scale play total by re-timing the rewritten
    # block. If the floor forced it above D (over-stuffed cue that physically
    # cannot fit), surface the planner-issue signal — but still render the
    # best-effort floored result; never drop or mangle content.
    cannot_fit = False
    if hit_floor:
        scaled_tree = _parse_cue_block(new_block)
        if scaled_tree is not None:
            scaled_total, _ = _time_for_statements(scaled_tree.body, helpers)
            # "Cannot fit" means the floored animation still overruns the
            # narration itself — compare against ``expected`` directly, not the
            # coarse 1.0s drift tolerance (which would mask a 0.5s overflow on a
            # 0.2s cue). A small epsilon avoids float-noise false positives.
            cannot_fit = scaled_total > expected + 1e-6

    if cannot_fit:
        msg = (
            f"CUE {cue_idx}: cannot fit {play_total:.2f}s of animation into "
            f"{expected:.2f}s narration even at the {_MIN_RUN_TIME}s run_time "
            f"floor — likely planner issue (cue over-stuffed). Floored "
            f"run_times and rendered best-effort; no content dropped."
        )
    else:
        verb = "stretched" if factor > 1 else "compressed"
        msg = (
            f"CUE {cue_idx}: {verb} {len(literals)} run_time(s) by "
            f"{factor:.3f}x (animations {play_total:.2f}s → {expected:.2f}s "
            f"to match narration)"
        )
    return new_code, msg


def auto_fix_timing(
    code: str,
    cue_durations: list[float],
) -> tuple[str, list[str]]:
    """Attempt to fix timing mismatches by adjusting ``self.wait()`` calls.

    Strategy: for each cue block, find the LAST ``self.wait(X)`` call and
    adjust its argument so the total block time matches the expected duration.
    When a cue block has NO ``self.wait()`` at all but falls short of its
    expected duration by more than the tolerance, INSERT ``self.wait(residual)``
    at the cue boundary (issue #22 — deterministic residual resolver). The
    verifier knows the exact residual, so it writes it directly instead of
    handing the LLM a hint.

    This handles the common case where the Director's wait arithmetic is wrong
    but the animation structure is correct.

    Before the wait-adjustment pass, any CUE block with index >= len(cue_durations)
    is physically deleted. Those phantom blocks are the Section-2-style failure
    mode where every retry regenerates the same invalid cue and burns the budget.

    Returns (fixed_code, list_of_applied_fixes).
    """
    applied: list[str] = []
    if not _has_cue_markers(code) and len(cue_durations) != 1:
        return code, applied  # nothing to attribute time to; never guess
    fixed_code, phantom_fixes = _strip_phantom_cue_blocks(code, len(cue_durations))
    applied.extend(phantom_fixes)

    cue_blocks = _split_into_cue_blocks(fixed_code)
    if not cue_blocks:
        return fixed_code, applied

    helpers = _timed_helper_names(fixed_code)

    # Process cue blocks in REVERSE order so that rfind(block_code) on the full
    # code always matches the correct (last remaining unmodified) occurrence.
    for cue_idx, block_code in reversed(cue_blocks):
        if cue_idx >= len(cue_durations):
            continue

        parsed = _parse_block_with_shift(block_code)
        if parsed is None:
            continue
        tree, top_stmts, line_shift, col_shift = parsed
        all_stmts = tree.body
        expected = cue_durations[cue_idx]

        # The adjustable wait is the LAST TOP-LEVEL ``self.wait(...)`` statement
        # of the cue. A wait nested in a loop or branch runs a different number
        # of times than once, so it is never the one to rewrite.
        last_wait: ast.Call | None = None
        for stmt in top_stmts:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Call)
                and _is_self_wait(stmt.value)
            ):
                last_wait = stmt.value
        has_any_wait = any(
            isinstance(n, ast.Call) and _is_self_wait(n) for n in ast.walk(tree)
        )

        if last_wait is None:
            if not has_any_wait:
                # No self.wait() to absorb the gap. This is issue #59's exact
                # failure mode: a continuous animation whose wall-clock differs
                # from its narration with no wait to stretch. PRIMARY fix: scale
                # the self.play(run_time=...) literals so the animation fills D.
                scaled_code, scale_msg = _try_scale_plays(
                    fixed_code, block_code, cue_idx, all_stmts, expected, helpers
                )
                if scale_msg:
                    fixed_code = scaled_code
                    applied.append(scale_msg)
                    continue

            # FALLBACK (#22 + #23): no top-level wait to adjust and scaling did
            # not apply. If the animations fall short of D by more than
            # tolerance, INSERT a self.wait(residual) after the cue's last
            # top-level statement. Skip dynamic blocks (residual unknowable)
            # and on-time/overruns.
            block_total, block_unknown = _time_for_statements(all_stmts, helpers)
            if block_unknown:
                continue
            residual = expected - block_total
            if residual <= _MIN_INSERT_RESIDUAL:
                continue
            new_code, msg = _insert_missing_wait(
                fixed_code, block_code, cue_idx, residual
            )
            if msg:
                fixed_code = new_code
                applied.append(msg)
            continue

        # If the wait's duration is itself dynamic (UNKNOWN), we cannot compute
        # a safe replacement — leave it untouched (issue #23).
        last_wait_old_val = _get_wait_duration(last_wait)
        if isinstance(last_wait_old_val, _Unknown):
            continue

        # If any OTHER duration in the block is dynamic, the residual is
        # unknowable — adjusting the wait would over- or under-shoot. Skip.
        full_total, block_unknown = _time_for_statements(all_stmts, helpers)
        if block_unknown:
            continue
        total_without_last_wait = full_total - last_wait_old_val
        new_wait = max(0.01, expected - total_without_last_wait)

        if abs(new_wait - last_wait_old_val) < 0.05:
            continue  # difference is negligible, don't bother

        # Replace exactly the source span of that call, located inside this
        # cue's block. A text search for "self.wait(1.0)" over the whole file
        # edited whichever cue happened to contain the same text last, and a
        # regex could not handle nested parentheses or a wait named in a comment.
        span = _node_span(block_code, last_wait, line_shift, col_shift)
        block_idx = fixed_code.rfind(block_code)
        if span is None or block_idx < 0:
            continue
        fixed_code = (
            fixed_code[: block_idx + span[0]]
            + f"self.wait({new_wait:.2f})"
            + fixed_code[block_idx + span[1] :]
        )
        applied.append(
            f"CUE {cue_idx}: self.wait() adjusted from "
            f"{last_wait_old_val:.2f}s to {new_wait:.2f}s "
            f"(expected total {expected:.2f}s, anims={total_without_last_wait:.2f}s)"
        )

    # Never hand back code the edits broke: a file that no longer compiles is
    # worse than a timing mismatch.
    if applied and fixed_code != code:
        try:
            ast.parse(code)
        except (SyntaxError, ValueError):
            pass  # input was already broken; nothing to protect
        else:
            try:
                ast.parse(fixed_code)
            except (SyntaxError, ValueError):
                logger.warning(
                    "[timing_verifier] auto-fix produced code that does not "
                    "parse; discarding %d edit(s)",
                    len(applied),
                )
                return code, []

    if applied:
        for fix in applied:
            logger.info("[timing_verifier] auto-fix: %s", fix)

    return fixed_code, applied
