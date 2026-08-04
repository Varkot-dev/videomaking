import enum
import logging
import os
import re
import subprocess

from manimgen import paths
from manimgen.llm import chat
from manimgen.utils import strip_fencing
from manimgen.validator.codeguard import (
    apply_error_aware_fixes,
    precheck_and_autofix_file,
)
from manimgen.validator.env import get_render_env
from manimgen.validator.layout_checker import check_layout
from manimgen.validator.render_command import build_manimgl_command
from manimgen.validator.runner import (
    _find_rendered_video,
    _is_3d_scene,
    _render_floor,
)
from manimgen.validator.timing_verifier import auto_fix_timing, verify_timing

logger = logging.getLogger(__name__)

MAX_RETRIES = paths.render_max_retries()

# ---------------------------------------------------------------------------
# LLM spend budgets
# ---------------------------------------------------------------------------
#
# READ THIS BEFORE SETTING ANY OF THESE ENV VARS — the scopes differ.
#
# MANIMGEN_MAX_RETRY_LLM_CALLS (MAX_LLM_FIX_CALLS)
#     PER-SECTION, PER-CATEGORY seed value. It is NOT a total. It sets the
#     default for the two independent per-category counters below, each of
#     which is reset every time `retry_scene` is called — and `cli.py` calls
#     `retry_scene` once per section.
#
# MANIMGEN_MAX_ERROR_LLM_CALLS / MANIMGEN_MAX_VISUAL_LLM_CALLS
#     PER-SECTION caps on error-repair and visual-repair calls respectively.
#     They are deliberately independent so an error storm cannot bankrupt
#     visual correction (and vice versa).
#
# Worst-case spend for ONE section is therefore roughly:
#     MAX_ERROR_LLM_FIX_CALLS + MAX_VISUAL_LLM_FIX_CALLS
# and a video with N sections multiplies that by N. With the default of 2 that
# is ~4 fix calls plus vision calls per section — far more than "2" suggests.
#
# MANIMGEN_MAX_TOTAL_LLM_CALLS (MAX_TOTAL_LLM_CALLS)
#     The only GLOBAL, PER-RUN cap: total paid retry LLM calls across ALL
#     sections and BOTH categories. This is the number to set if you want a
#     hard ceiling on what a run can spend. It defaults high enough that normal
#     runs are unaffected — it is a backstop against runaway spend, not a
#     tightening of existing behaviour.
MAX_LLM_FIX_CALLS = int(
    os.environ.get("MANIMGEN_MAX_RETRY_LLM_CALLS", str(MAX_RETRIES))
)
MAX_ERROR_LLM_FIX_CALLS = int(
    os.environ.get("MANIMGEN_MAX_ERROR_LLM_CALLS", str(MAX_LLM_FIX_CALLS))
)
MAX_VISUAL_LLM_FIX_CALLS = int(
    os.environ.get("MANIMGEN_MAX_VISUAL_LLM_CALLS", str(MAX_LLM_FIX_CALLS))
)

_DEFAULT_MAX_TOTAL_LLM_CALLS = 200
MAX_TOTAL_LLM_CALLS = int(
    os.environ.get("MANIMGEN_MAX_TOTAL_LLM_CALLS", str(_DEFAULT_MAX_TOTAL_LLM_CALLS))
)

# Global spend counter for the current run. Module-level because the budget is
# per-RUN and must survive across the many per-section retry_scene() calls that
# the per-category counters deliberately do not.
_run_llm_calls_used = 0


def reset_run_budget() -> None:
    """Reset the global per-run LLM budget.

    Call once at the start of a run. Tests also use this for isolation.
    """
    global _run_llm_calls_used
    _run_llm_calls_used = 0


def run_budget_used() -> int:
    """Paid retry LLM calls consumed so far in this run, across all sections."""
    return _run_llm_calls_used


def _consume_run_budget(category: str) -> bool:
    """Try to reserve one paid LLM call against the global per-run budget.

    Returns True if the call is authorised (and charges it), False if the
    global budget is exhausted. Both outcomes are logged so spend is
    observable rather than silent.
    """
    global _run_llm_calls_used
    if _run_llm_calls_used >= MAX_TOTAL_LLM_CALLS:
        logger.warning(
            "[retry] GLOBAL LLM budget exhausted: %d/%d calls used this run "
            "(MANIMGEN_MAX_TOTAL_LLM_CALLS) — refusing %s fix call",
            _run_llm_calls_used,
            MAX_TOTAL_LLM_CALLS,
            category,
        )
        return False
    _run_llm_calls_used += 1
    logger.info(
        "[retry] Global LLM budget: %d/%d calls used this run (charged: %s fix)",
        _run_llm_calls_used,
        MAX_TOTAL_LLM_CALLS,
        category,
    )
    return True


RETRY_ERROR_SIGNATURE_CHARS = 500
RETRY_PROMPT_STDERR_CHARS = 3000
RETRY_PROMPT_CODE_CHARS = 7000


class SceneErrorType(str, enum.Enum):
    """Classifies ManimGL render errors for targeted fix guidance.

    Inherits str so instances compare equal to their string values and can be
    used transparently as dict keys without extra conversion.
    """

    PRECHECK_VGROUP = "precheck_vgroup"
    SYNTAX = "syntax"
    IMPORT = "import"
    ATTRIBUTE = "attribute"
    TYPE = "type"
    RUNTIME = "runtime"
    TIMING = "timing"


def _classify_error(stderr: str) -> SceneErrorType:
    # Check precheck-specific errors BEFORE generic TypeError/AttributeError —
    # precheck output contains the word "TypeError" in its explanation text,
    # which would otherwise cause a false match on the generic TypeError branch.
    if (
        "Precheck failed" in stderr
        and "VGroup" in stderr
        and "item assignment" in stderr
    ):
        return SceneErrorType.PRECHECK_VGROUP
    if "SyntaxError" in stderr:
        return SceneErrorType.SYNTAX
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return SceneErrorType.IMPORT
    if "AttributeError" in stderr:
        return SceneErrorType.ATTRIBUTE
    if "TypeError" in stderr:
        return SceneErrorType.TYPE
    return SceneErrorType.RUNTIME


_FIX_GUIDANCE: dict[SceneErrorType, str] = {
    SceneErrorType.PRECHECK_VGROUP: (
        "Fix VGroup item assignment. VGroup does NOT support boxes[i] = x or "
        "boxes[i], boxes[j] = boxes[j], boxes[i] — these raise TypeError at runtime. "
        "The correct pattern: before any swaps, create a parallel Python list: "
        "box_list = list(boxes); label_list = list(labels). "
        "Then swap the list references: box_list[i], box_list[j] = box_list[j], box_list[i]. "
        "Use box_list[k].get_center() for position lookups after swaps. "
        "Never assign into the VGroup directly. Never use boxes[i] after the first swap."
    ),
    SceneErrorType.SYNTAX: "Fix the Python syntax error shown in the traceback.",
    SceneErrorType.IMPORT: "Fix the import. Use `from manimlib import *`. Do not import from `manim`.",
    SceneErrorType.ATTRIBUTE: "Fix the attribute error. Check the correct ManimGL method name and signature.",
    SceneErrorType.TYPE: "Fix the type error. Check argument types and counts for the method.",
    SceneErrorType.RUNTIME: "Simplify the scene logic. Reduce animations, check object creation order.",
    SceneErrorType.TIMING: (
        "Fix the animation timing. Each CUE block must have animations + self.wait() that "
        "sum exactly to the cue duration. The most common bug: loop timing — subtract the "
        "TOTAL loop run_time (n × per_iter), not just one iteration. Use an accumulator: "
        "anim_time += run_time inside the loop, then self.wait(max(0.01, cue_dur - anim_time))."
    ),
}


def _fix_guidance(error_type: SceneErrorType, stderr: str = "") -> str:
    base = _FIX_GUIDANCE.get(error_type, "Fix the error shown in the traceback.")
    if error_type == SceneErrorType.TYPE and "unexpected keyword argument" in stderr:
        kw_m = re.search(r"got an unexpected keyword argument '(\w+)'", stderr)
        hint_m = re.search(r"Did you mean '(\w+)'\?", stderr)
        method_m = re.search(r"(\w+)\(\) got an unexpected keyword argument", stderr)
        method = method_m.group(1) if method_m else "the method"
        if kw_m:
            bad = kw_m.group(1)
            hint = (
                f" The correct kwarg name is '{hint_m.group(1)}'."
                if hint_m
                else f" Check the exact ManimGL signature for {method}()."
            )
            return (
                f"Fix the TypeError: '{bad}' is not a valid keyword argument for {method}().{hint} "
                f"Do NOT guess — look up the exact ManimGL signature for {method}() and fix ALL "
                f"kwargs in that call to use the correct parameter names in one pass."
            )
    return base


def _build_error_signature(error_type: str, stderr: str) -> str:
    # For unexpected-kwarg TypeErrors, normalize to method name so that peeling
    # rows= then cols= from the same call is recognized as one root cause.
    if "unexpected keyword argument" in stderr:
        method_m = re.search(r"(\w+)\(\) got an unexpected keyword argument", stderr)
        if method_m:
            return f"{error_type}:unexpected_kwarg:{method_m.group(1)}"
    normalized = re.sub(r"\s+", " ", stderr).strip()
    return f"{error_type}:{normalized[:RETRY_ERROR_SIGNATURE_CHARS]}"


def _build_visual_signature(issues: list[str]) -> str:
    """Stable signature for a set of visual defects.

    The visual counterpart of _build_error_signature. Order-insensitive (the
    checker may report the same defects in a different order) and whitespace-
    normalised, so an unchanged defect set is recognised as a repeat and does
    not burn a second paid vision call.
    """
    normalized = sorted(
        re.sub(r"\s+", " ", line).strip() for line in issues if line.strip()
    )
    return "|".join(normalized)[:RETRY_ERROR_SIGNATURE_CHARS]


def _truncate_for_prompt(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head_chars = max_chars // 2
    tail_chars = max_chars - head_chars
    return (
        text[:head_chars]
        + "\n\n...[truncated for token efficiency]...\n\n"
        + text[-tail_chars:]
    )


def apply_timing_gate(
    code: str,
    scene_path: str,
    cue_durations: list[float],
) -> tuple[str, list[str]]:
    """Run timing verification and auto-fix on the current code.

    Returns (possibly_fixed_code, remaining_warnings).
    The code is written back to scene_path if auto-fixes were applied.
    """
    result = verify_timing(code, cue_durations)
    if result["ok"]:
        return code, []

    fixed, fixes_applied = auto_fix_timing(code, cue_durations)
    if fixes_applied:
        with open(scene_path, "w") as f:
            f.write(fixed)
        for fix in fixes_applied:
            print(f"[retry] timing auto-fix: {fix}")
        code = fixed

    # Re-verify after auto-fix — some issues may persist (e.g. unresolvable
    # loop counts, dynamic variables in run_time).
    recheck = verify_timing(code, cue_durations)
    return code, recheck.get("warnings", [])


def retry_scene(
    section: dict,
    original_code: str,
    class_name: str,
    scene_path: str,
    cue_durations: list[float] | None = None,
) -> tuple[bool, str | None]:
    """
    Retry generating and running a scene up to MAX_RETRIES times.
    Each retry sends the original code + error back to the LLM for a fix.
    Returns (success, video_path).
    """
    code = original_code
    logs_dir = paths.logs_dir()
    os.makedirs(logs_dir, exist_ok=True)
    system_prompt = _load_retry_system_prompt()
    # NOTE ON SCOPE: these two counters are PER-SECTION — they are re-zeroed on
    # every retry_scene() call, and cli.py calls retry_scene() once per section.
    # They bound this section's spend only. The GLOBAL per-run ceiling across
    # all sections is MAX_TOTAL_LLM_CALLS, enforced via _consume_run_budget().
    section_error_llm_calls_used = 0
    section_visual_llm_calls_used = 0
    seen_error_signatures: set[str] = set()
    # Visual-defect dedup, mirroring seen_error_signatures on the error path.
    # Without it an unfixable visual defect burns one paid VISION call per
    # attempt (vision calls are the most expensive thing this loop can do).
    seen_visual_signatures: set[str] = set()
    # Timing warnings carried forward into the next LLM fix prompt so the
    # model is aware of freeze-frame-tail risk even when it is fixing an
    # unrelated error. Previously these were only printed and lost.
    pending_timing_warnings: list[str] = []

    # Track the best rendered video across attempts. Later attempts that fail
    # to render at all must not evict an earlier successful render — otherwise
    # one bad LLM fix between attempt 2 and 5 drags the section to fallback.
    best_video_path: str | None = None
    best_issue_count: int = 10**9

    # Timing pass on the initial code — catches freeze-frame tails before the
    # first render attempt at zero cost. (I6 · stable rhythm, I10 · narration contract)
    if cue_durations:
        code, initial_timing_warnings = apply_timing_gate(
            code, scene_path, cue_durations
        )
        if initial_timing_warnings:
            print(
                f"[retry] {len(initial_timing_warnings)} timing issue(s) found in initial code:"
            )
            for w in initial_timing_warnings:
                print(f"[retry]   {w}")

    for attempt in range(1, MAX_RETRIES + 1):
        result = _run_and_capture(scene_path, class_name)
        if result["success"]:
            from manimgen.validator.frame_checker import check_frames
            from manimgen.validator.timing_verifier import (
                blocking_freezes,
                join_frozen_with_timing,
                verify_timing,
            )

            frame_result = check_frames(result["video_path"])

            # HARD TIMING GATE (computed first so the frozen-frame join below
            # can consult it). timing_verifier QUANTIFIES freeze-frame tails:
            # a cue whose resolvable animation ends >= the freeze-block
            # threshold before its narration is a multi-second dead screen,
            # not a hold. UNKNOWN cues never block. Such a render must NOT be
            # accepted — feed it through the same defect→retry→best-attempt
            # machinery as visual defects. This closes the silent-failure: the
            # pipeline used to detect these freezes and ship anyway.
            freezes: list[str] = []
            if cue_durations:
                freezes = blocking_freezes(verify_timing(code, cue_durations))

            # #32: re-admit frame_checker's frozen-frame signal SAFELY by
            # joining it with the timing oracle. A frozen frame is a HARD
            # failure IFF timing confirms a dead tail (blocking freeze present);
            # for a legit final hold (narration still running over a static
            # visual) timing reports no freeze and the frozen signal is dropped.
            # Black frames / edge clipping always pass through. Previously every
            # frozen line was discarded unconditionally — the signal was
            # computed by frame_checker then silently thrown away.
            frame_issues = join_frozen_with_timing(
                frame_result.issues or [],
                timing_freeze_confirmed=bool(freezes),
            )

            combined_issues = list(frame_issues)
            defective_frames: list[str] = []

            if freezes:
                print(
                    f"[retry] Attempt {attempt}/{MAX_RETRIES} has "
                    f"{len(freezes)} blocking freeze-frame tail(s):"
                )
                for fz in freezes:
                    print(f"[retry]   {fz}")
                combined_issues.extend(freezes)

            # Gate the expensive LLM vision check: skip check_layout when the
            # zero-cost frame_checker (or the timing gate) already found
            # concrete defects (we have actionable issues and don't need a
            # paid second opinion). When clean, run check_layout to catch
            # defects frames can't see. Detection is never budget-gated.
            #
            # #33: layout_checker fails CLOSED. On its own LLM failure / empty
            # response it returns skipped=True — the render is UNVERIFIED, not
            # verified-clean. An UNVERIFIED render must NOT short-circuit to an
            # immediate clean acceptance (that is the silent false-accept during
            # an API outage), but it also has no concrete defect to fix, so it
            # must NOT burn the visual-fix budget or retry forever. We therefore
            # fall through to the bounded best-render acceptance below.
            layout_unverified = False
            if not combined_issues:
                layout = check_layout(result["video_path"])
                if layout.get("skipped"):
                    layout_unverified = True
                elif not layout["ok"] and layout["issues"]:
                    combined_issues.extend(layout["issues"].splitlines())
                defective_frames = layout.get("frames", [])

            if not combined_issues and not layout_unverified:
                return True, result["video_path"]

            # Record this render as the best-so-far if it has fewer issues
            # than anything we've rendered before. Future failing attempts
            # can fall back to this instead of triggering a title-card fallback.
            issue_count = len(combined_issues)
            if issue_count < best_issue_count:
                best_video_path = result["video_path"]
                best_issue_count = issue_count

            # #33: UNVERIFIED render (layout LLM was down) with NO concrete
            # frame/timing defect. There is nothing actionable to feed an LLM
            # fix, and re-rendering will keep hitting the same outage, so
            # spending the visual budget or retrying is pure churn. Bound it:
            # ship this render as the best-so-far WITHOUT claiming it passed the
            # layout gate. This avoids both the false-accept (we never returned
            # the immediate clean True above) and an infinite/expensive retry.
            if not combined_issues and layout_unverified:
                print(
                    f"[retry] Attempt {attempt}/{MAX_RETRIES}: layout check "
                    "UNVERIFIED (LLM unavailable) and no frame/timing defects — "
                    "shipping render unverified (bounded, no clean-pass claim)."
                )
                return True, best_video_path or result["video_path"]

            # Scene rendered but has visual defects. Feed structured feedback
            # back into the retry loop if budget allows.
            print(
                f"[retry] Attempt {attempt}/{MAX_RETRIES} rendered but has visual defects:"
            )
            for line in combined_issues:
                print(f"[retry]   {line}")

            if (
                section_visual_llm_calls_used >= MAX_VISUAL_LLM_FIX_CALLS
                or attempt == MAX_RETRIES
            ):
                print(
                    "[retry] Accepting video despite visual issues (budget or attempt limit reached)."
                )
                return True, best_video_path or result["video_path"]

            # Dedup: if we already paid for a vision call on this exact set of
            # defects and they came back unchanged, the model has nothing new
            # to offer. Mirrors the error path's seen_error_signatures.
            visual_signature = _build_visual_signature(combined_issues)
            if visual_signature in seen_visual_signatures:
                print(
                    "[retry] Repeated visual defect signature — a vision call "
                    "already failed to fix it; accepting best render instead of "
                    "paying again."
                )
                return True, best_video_path or result["video_path"]

            # Global per-run ceiling across ALL sections (MAX_TOTAL_LLM_CALLS).
            if not _consume_run_budget("visual"):
                print(
                    "[retry] Global run LLM budget exhausted — accepting best "
                    "render instead of requesting a visual fix."
                )
                return True, best_video_path or result["video_path"]

            print("[retry] Requesting visual fix from LLM...")
            code = _request_visual_fix(
                code, "\n".join(combined_issues), system_prompt, defective_frames
            )
            section_visual_llm_calls_used += 1
            seen_visual_signatures.add(visual_signature)
            with open(scene_path, "w") as f:
                f.write(code)
            precheck_and_autofix_file(scene_path)
            # Always reload — precheck may have applied auto-fixes in-place
            with open(scene_path) as f:
                code = f.read()
            # Timing pass — catch timing bugs in the LLM's visual fix
            if cue_durations:
                code, tw = apply_timing_gate(code, scene_path, cue_durations)
                pending_timing_warnings = tw
            continue

        error_type = _classify_error(result["stderr"])
        guidance = _fix_guidance(error_type, result["stderr"])
        error_signature = _build_error_signature(error_type, result["stderr"])
        _write_attempt_artifacts(logs_dir, class_name, attempt, code, result["stderr"])

        # Token-free deterministic fixes first.
        local_fixed, local_applied = apply_error_aware_fixes(code, result["stderr"])
        if local_applied and local_fixed != code:
            code = local_fixed
            with open(scene_path, "w") as f:
                f.write(code)
            print(
                f"[retry] Attempt {attempt}/{MAX_RETRIES} applied local fixes: {', '.join(local_applied)}"
            )
            continue

        print(
            f"[retry] Attempt {attempt}/{MAX_RETRIES} failed ({error_type}). Requesting fix..."
        )

        # The remaining cases (last attempt, repeated signature, budget
        # exhausted) all mean: there is no productive LLM fix we can make.
        # Re-rendering the unchanged code would only burn attempts producing
        # nothing, so break out (the best-render / fallback logic below
        # handles delivery) instead of spinning idle iterations.
        if attempt == MAX_RETRIES:
            break
        if error_signature in seen_error_signatures:
            print(
                f"[retry] Repeated error signature with no deterministic fix "
                f"available — stopping (attempt {attempt}/{MAX_RETRIES})."
            )
            break
        if section_error_llm_calls_used >= MAX_ERROR_LLM_FIX_CALLS:
            print(
                f"[retry] Per-section error-fix LLM budget reached "
                f"({MAX_ERROR_LLM_FIX_CALLS} calls for this section) — stopping."
            )
            break
        # Global per-run ceiling across ALL sections (MAX_TOTAL_LLM_CALLS).
        if not _consume_run_budget("error"):
            print(
                f"[retry] Global run LLM budget exhausted "
                f"({MAX_TOTAL_LLM_CALLS} calls) — stopping."
            )
            break

        prompt_stderr = _truncate_for_prompt(
            result["stderr"], RETRY_PROMPT_STDERR_CHARS
        )
        prompt_code = _truncate_for_prompt(code, RETRY_PROMPT_CODE_CHARS)
        timing_context = ""
        if pending_timing_warnings:
            timing_context = (
                "\n\nKnown timing issues detected in this code (fix these too "
                "to avoid freeze-frame tails):\n- "
                + "\n- ".join(pending_timing_warnings)
            )
        fixed = chat(
            system=system_prompt,
            user=f"""This ManimGL scene failed to render. Fix it.

Error type: {error_type}
Guidance: {guidance}

Full error:
{prompt_stderr}{timing_context}

Original code:
{prompt_code}""",
        )
        section_error_llm_calls_used += 1
        seen_error_signatures.add(error_signature)

        fixed = strip_fencing(fixed)

        code = fixed
        with open(scene_path, "w") as f:
            f.write(code)

        # Local auto-fixes are free and often resolve common ManimGL mismatches.
        # Always reload — precheck may have applied auto-fixes in-place.
        precheck_and_autofix_file(scene_path)
        with open(scene_path) as f:
            code = f.read()

        # Timing pass — auto-fix self.wait() values and inject remaining
        # timing warnings into the next attempt's error context.
        if cue_durations:
            code, timing_warnings = apply_timing_gate(code, scene_path, cue_durations)
            # Carry into the NEXT LLM fix prompt so the model is aware of
            # freeze-frame-tail risk even while fixing an unrelated error.
            # Previously these were only printed and silently lost.
            pending_timing_warnings = timing_warnings
            if timing_warnings:
                print(
                    f"[retry] {len(timing_warnings)} timing warning(s) after auto-fix:"
                )
                for w in timing_warnings:
                    print(f"[retry]   {w}")

    # All attempts exhausted. If an earlier attempt rendered successfully
    # (even with some visual defects), prefer it over the title-card fallback.
    if best_video_path:
        print(
            f"[retry] All attempts exhausted — shipping best earlier render (had {best_issue_count} visual issue(s))."
        )
        return True, best_video_path
    return False, None


def _run_and_capture(scene_path: str, class_name: str) -> dict:
    precheck = precheck_and_autofix_file(scene_path)
    if not precheck["ok"]:
        stderr = precheck["stderr"]
        if precheck.get("layout_warnings"):
            stderr += "\nLayout warnings:\n- " + "\n- ".join(
                precheck["layout_warnings"]
            )
        return {"success": False, "video_path": None, "stderr": stderr}

    # Director scenes can be long; avoid false timeout-driven fallbacks.
    timeout = 360 if _is_3d_scene(scene_path) else 240
    # Freshness floor: without it, an attempt whose render silently produced no
    # file would pick up the PREVIOUS attempt's video and validate that instead
    # — shipping the pre-fix render while we pay for a fix that never landed.
    render_started_at = _render_floor()
    try:
        result = subprocess.run(
            build_manimgl_command(scene_path, class_name),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=get_render_env(),
        )
        if result.returncode == 0:
            return {
                "success": True,
                "video_path": _find_rendered_video(
                    class_name, newer_than=render_started_at
                ),
                "stderr": "",
            }
        return {"success": False, "video_path": None, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"success": False, "video_path": None, "stderr": "TimeoutExpired"}


_retry_system_prompt_cache: str | None = None


def _load_retry_system_prompt() -> str:
    global _retry_system_prompt_cache
    if _retry_system_prompt_cache is None:
        here = os.path.dirname(__file__)
        root = os.path.dirname(here)
        retry_system_path = os.path.join(here, "prompts", "retry_system.md")
        director_system_path = os.path.join(
            root, "generator", "prompts", "director_system.md"
        )
        with open(retry_system_path) as f:
            system = f.read()
        with open(director_system_path) as f:
            director = f.read()
        _retry_system_prompt_cache = system.strip() + "\n\n" + director
    return _retry_system_prompt_cache


def _request_visual_fix(
    code: str, issues: str, system_prompt: str, frames: list[str]
) -> str:
    """Ask the LLM to fix code based on structured visual feedback and defective frames."""
    from manimgen.utils import load_reference_frames

    prompt_code = _truncate_for_prompt(code, RETRY_PROMPT_CODE_CHARS)
    ref_frames = load_reference_frames()

    fixed = chat(
        system=system_prompt,
        user=f"""This ManimGL scene rendered successfully but has visual defects detected by frame analysis.

Visual defects found (ISSUE | CAUSE | FIX format):
{issues}

Fix the code to resolve these visual defects. Return only the corrected Python — no markdown.

Original code:
{prompt_code}""",
        images=ref_frames + frames,
    )
    return strip_fencing(fixed)


def _write_attempt_artifacts(
    logs_dir: str, class_name: str, attempt: int, code: str, stderr: str
) -> None:
    code_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.py")
    log_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.log")

    with open(code_path, "w") as f:
        f.write(code)
    with open(log_path, "w") as f:
        f.write(stderr)
