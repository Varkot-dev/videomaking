import enum
import re
import subprocess
import os
from manimgen.llm import chat
from manimgen.validator.runner import _find_rendered_video, _is_3d_scene
from manimgen.validator.codeguard import precheck_and_autofix_file as precheck_and_autofix, apply_error_aware_fixes
from manimgen.validator.env import get_render_env
from manimgen.validator.layout_checker import check_layout
from manimgen import paths

MAX_RETRIES = paths.render_max_retries()
MAX_LLM_FIX_CALLS = int(os.environ.get("MANIMGEN_MAX_RETRY_LLM_CALLS", str(MAX_RETRIES)))
RETRY_ERROR_SIGNATURE_CHARS = 500
RETRY_PROMPT_STDERR_CHARS = 3000
RETRY_PROMPT_CODE_CHARS = 7000


class SceneErrorType(str, enum.Enum):
    """Classifies ManimGL render errors for targeted fix guidance.

    Inherits str so instances compare equal to their string values and can be
    used transparently as dict keys without extra conversion.
    """
    PRECHECK_VGROUP = "precheck_vgroup"
    SYNTAX          = "syntax"
    IMPORT          = "import"
    ATTRIBUTE       = "attribute"
    TYPE            = "type"
    RUNTIME         = "runtime"


def _classify_error(stderr: str) -> SceneErrorType:
    # Check precheck-specific errors BEFORE generic TypeError/AttributeError —
    # precheck output contains the word "TypeError" in its explanation text,
    # which would otherwise cause a false match on the generic TypeError branch.
    if "Precheck failed" in stderr and "VGroup" in stderr and "item assignment" in stderr:
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
    SceneErrorType.SYNTAX:    "Fix the Python syntax error shown in the traceback.",
    SceneErrorType.IMPORT:    "Fix the import. Use `from manimlib import *`. Do not import from `manim`.",
    SceneErrorType.ATTRIBUTE: "Fix the attribute error. Check the correct ManimGL method name and signature.",
    SceneErrorType.TYPE:      "Fix the type error. Check argument types and counts for the method.",
    SceneErrorType.RUNTIME:   "Simplify the scene logic. Reduce animations, check object creation order.",
}


def _fix_guidance(error_type: SceneErrorType) -> str:
    return _FIX_GUIDANCE.get(error_type, "Fix the error shown in the traceback.")


def _build_error_signature(error_type: str, stderr: str) -> str:
    normalized = re.sub(r"\s+", " ", stderr).strip()
    return f"{error_type}:{normalized[:RETRY_ERROR_SIGNATURE_CHARS]}"


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


def retry_scene(section: dict, original_code: str, class_name: str, scene_path: str) -> tuple[bool, str | None]:
    """
    Retry generating and running a scene up to MAX_RETRIES times.
    Each retry sends the original code + error back to the LLM for a fix.
    Returns (success, video_path).
    """
    code = original_code
    logs_dir = paths.logs_dir()
    os.makedirs(logs_dir, exist_ok=True)
    system_prompt = _load_retry_system_prompt()
    llm_fix_calls_used = 0
    seen_error_signatures: set[str] = set()

    for attempt in range(1, MAX_RETRIES + 1):
        result = _run_and_capture(scene_path, class_name)
        if result["success"]:
            from manimgen.validator.frame_checker import check_frames
            frame_result = check_frames(result["video_path"])
            
            layout = check_layout(result["video_path"])
            
            combined_issues = []
            if not frame_result.ok:
                combined_issues.extend(frame_result.issues_text.splitlines())
            if not layout["ok"] and layout["issues"]:
                combined_issues.extend(layout["issues"].splitlines())

            if not combined_issues:
                return True, result["video_path"]

            # Scene rendered but has visual defects. Feed structured feedback
            # back into the retry loop if budget allows.
            issues_text = "\n".join(combined_issues)
            print(f"[retry] Attempt {attempt}/{MAX_RETRIES} rendered but has visual defects:")
            for line in combined_issues:
                print(f"[retry]   {line}")

            if llm_fix_calls_used >= MAX_LLM_FIX_CALLS:
                print("[retry] LLM retry budget exhausted — accepting video despite visual issues.")
                return True, result["video_path"]

            print("[retry] Requesting visual fix from LLM...")
            
            # Extract only the FIX instruction for the code-fixing LLM
            fix_only = []
            for line in combined_issues:
                if "FIX:" in line:
                    parts = line.split("FIX:")
                    fix_only.append("- " + parts[-1].strip())
                else:
                    fix_only.append("- " + line.strip())
            
            code = _request_visual_fix(code, "\n".join(fix_only), system_prompt)
            llm_fix_calls_used += 1
            with open(scene_path, "w") as f:
                f.write(code)
            precheck_and_autofix(scene_path)
            # Always reload — precheck may have applied auto-fixes in-place
            with open(scene_path) as f:
                code = f.read()
            continue

        error_type = _classify_error(result["stderr"])
        guidance = _fix_guidance(error_type)
        error_signature = _build_error_signature(error_type, result["stderr"])
        _write_attempt_artifacts(logs_dir, class_name, attempt, code, result["stderr"])

        # Token-free deterministic fixes first.
        local_fixed, local_applied = apply_error_aware_fixes(code, result["stderr"])
        if local_applied and local_fixed != code:
            code = local_fixed
            with open(scene_path, "w") as f:
                f.write(code)
            print(f"[retry] Attempt {attempt}/{MAX_RETRIES} applied local fixes: {', '.join(local_applied)}")
            continue

        print(f"[retry] Attempt {attempt}/{MAX_RETRIES} failed ({error_type}). Requesting fix...")

        # Stop making token calls after the final failed attempt.
        if attempt == MAX_RETRIES:
            break

        # Avoid paying tokens repeatedly for the same failure mode.
        if error_signature in seen_error_signatures:
            print(
                f"[retry] Skipping LLM fix for repeated error signature "
                f"(attempt {attempt}/{MAX_RETRIES})."
            )
            continue

        if llm_fix_calls_used >= MAX_LLM_FIX_CALLS:
            print(
                f"[retry] LLM retry budget reached ({MAX_LLM_FIX_CALLS} calls). "
                f"Skipping token call."
            )
            continue

        prompt_stderr = _truncate_for_prompt(result["stderr"], RETRY_PROMPT_STDERR_CHARS)
        prompt_code = _truncate_for_prompt(code, RETRY_PROMPT_CODE_CHARS)
        fixed = chat(
            system=system_prompt,
            user=f"""This ManimGL scene failed to render. Fix it.

Error type: {error_type}
Guidance: {guidance}

Full error:
{prompt_stderr}

Original code:
{prompt_code}""",
        )
        llm_fix_calls_used += 1
        seen_error_signatures.add(error_signature)

        if fixed.startswith("```"):
            fixed = re.sub(r"^```\w*\n?", "", fixed)
            fixed = re.sub(r"\n?```$", "", fixed)

        code = fixed
        with open(scene_path, "w") as f:
            f.write(code)

        # Local auto-fixes are free and often resolve common ManimGL mismatches.
        # Always reload — precheck may have applied auto-fixes in-place.
        precheck_and_autofix(scene_path)
        with open(scene_path) as f:
            code = f.read()

    return False, None


def _run_and_capture(scene_path: str, class_name: str) -> dict:
    precheck = precheck_and_autofix(scene_path)
    if not precheck["ok"]:
        stderr = precheck["stderr"]
        if precheck.get("layout_warnings"):
            stderr += "\nLayout warnings:\n- " + "\n- ".join(precheck["layout_warnings"])
        return {"success": False, "video_path": None, "stderr": stderr}

    # Director scenes can be long; avoid false timeout-driven fallbacks.
    timeout = 360 if _is_3d_scene(scene_path) else 240
    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", paths.render_quality_flag(), "-c", "#1C1C1C"],
            capture_output=True,
            text=True,
            timeout=timeout,
            env=get_render_env(),
        )
        if result.returncode == 0:
            return {"success": True, "video_path": _find_rendered_video(class_name), "stderr": ""}
        return {"success": False, "video_path": None, "stderr": result.stderr}
    except subprocess.TimeoutExpired:
        return {"success": False, "video_path": None, "stderr": "TimeoutExpired"}


def _load_retry_system_prompt() -> str:
    here = os.path.dirname(__file__)
    root = os.path.dirname(here)
    retry_system_path = os.path.join(here, "prompts", "retry_system.md")
    director_system_path = os.path.join(root, "generator", "prompts", "director_system.md")
    with open(retry_system_path) as f:
        system = f.read()
    with open(director_system_path) as f:
        director = f.read()
    return system.strip() + "\n\n" + director


def _request_visual_fix(code: str, issues: str, system_prompt: str) -> str:
    """Ask the LLM to fix code based on structured visual feedback from layout_checker."""
    prompt_code = _truncate_for_prompt(code, RETRY_PROMPT_CODE_CHARS)
    fixed = chat(
        system=system_prompt,
        user=f"""This ManimGL scene rendered successfully but has visual defects detected by frame analysis.

Visual defects found (ISSUE | CAUSE | FIX format):
{issues}

Fix the code to resolve these visual defects. Return only the corrected Python — no markdown.

Original code:
{prompt_code}""",
    )
    if fixed.startswith("```"):
        fixed = re.sub(r"^```\w*\n?", "", fixed)
        fixed = re.sub(r"\n?```$", "", fixed)
    return fixed


def _write_attempt_artifacts(logs_dir: str, class_name: str, attempt: int, code: str, stderr: str) -> None:
    code_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.py")
    log_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.log")

    with open(code_path, "w") as f:
        f.write(code)
    with open(log_path, "w") as f:
        f.write(stderr)
