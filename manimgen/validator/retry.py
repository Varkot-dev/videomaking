import re
import subprocess
import os
from manimgen.llm import chat
from manimgen.validator.runner import _find_rendered_video, _is_3d_scene
from manimgen.validator.codeguard import precheck_and_autofix_file as precheck_and_autofix, apply_error_aware_fixes
from manimgen.validator.env import get_render_env
from manimgen.validator.layout_checker import check_layout
from manimgen import paths

MAX_RETRIES = 3
MAX_LLM_FIX_CALLS = int(os.environ.get("MANIMGEN_MAX_RETRY_LLM_CALLS", "1"))
MAX_SPEC_RETRIES = 2
RETRY_ERROR_SIGNATURE_CHARS = 500
RETRY_PROMPT_STDERR_CHARS = 3000
RETRY_PROMPT_CODE_CHARS = 7000


def _classify_error(stderr: str) -> str:
    if "SyntaxError" in stderr:
        return "syntax"
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return "import"
    if "AttributeError" in stderr:
        return "attribute"
    if "TypeError" in stderr:
        return "type"
    return "runtime"


def _fix_guidance(error_type: str) -> str:
    guidance = {
        "syntax": "Fix the Python syntax error shown in the traceback.",
        "import": "Fix the import. Use `from manimlib import *`. Do not import from `manim`.",
        "attribute": "Fix the attribute error. Check the correct ManimGL method name and signature.",
        "type": "Fix the type error. Check argument types and counts for the method.",
        "runtime": "Simplify the scene logic. Reduce animations, check object creation order.",
    }
    return guidance.get(error_type, "Fix the error shown in the traceback.")


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


def retry_spec(
    section: dict,
    cue_index: int | None,
    total_cues: int | None,
    duration_seconds: float,
    errors: list[str],
) -> dict:
    """Re-ask LLM for a valid spec when validation fails. Budget: MAX_SPEC_RETRIES."""
    import json, re

    here = os.path.dirname(__file__)
    spec_prompt_path = os.path.join(here, "..", "generator", "prompts", "spec_system.md")
    with open(os.path.normpath(spec_prompt_path)) as f:
        system = f.read()
    error_block = "\n".join(errors)
    user_message = f"""Section title: {section['title']}
Visual description: {section['visual_description']}
Key objects: {', '.join(section.get('key_objects', []))}
Duration: {duration_seconds:.2f} seconds

Your previous spec was invalid. Fix the following errors and output only valid JSON:

{error_block}

Pick the most appropriate template. Output only valid JSON.
"""
    if cue_index is not None and total_cues and total_cues > 1:
        user_message += f"\nCUE: This is segment {cue_index + 1} of {total_cues}. Animate only the relevant part."

    raw = chat(system=system, user=user_message)

    def _safe_json_loads(s):
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', s)
            return json.loads(sanitized)

    from manimgen.utils import strip_fencing
    return _safe_json_loads(strip_fencing(raw))


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
            layout = check_layout(result["video_path"])
            if layout["ok"] or layout["skipped"]:
                return True, result["video_path"]
            # Scene rendered but has layout problems — treat as a soft failure
            print(f"[retry] Attempt {attempt}/{MAX_RETRIES} rendered but has layout issues:")
            for line in layout["issues"].splitlines():
                print(f"[retry]   {line}")
            if attempt == MAX_RETRIES or llm_fix_calls_used >= MAX_LLM_FIX_CALLS:
                # Accept the video rather than fallback — layout is better than nothing
                print("[retry] Accepting video despite layout issues (retry budget exhausted)")
                return True, result["video_path"]
            # Ask LLM to fix layout
            prompt_code = _truncate_for_prompt(code, RETRY_PROMPT_CODE_CHARS)
            fixed = chat(
                system=system_prompt,
                user=f"""This ManimGL scene rendered successfully but has visual layout problems.
Fix the layout issues described below. Do not change the mathematical content.

Layout issues:
{layout["issues"]}

Current code:
{prompt_code}""",
            )
            llm_fix_calls_used += 1
            if fixed.startswith("```"):
                fixed = re.sub(r"^```\w*\n?", "", fixed)
                fixed = re.sub(r"\n?```$", "", fixed)
            code = fixed
            with open(scene_path, "w") as f:
                f.write(code)
            precheck = precheck_and_autofix(scene_path)
            if precheck.get("applied_fixes"):
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
        precheck = precheck_and_autofix(scene_path)
        if precheck.get("applied_fixes"):
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

    timeout = 300 if _is_3d_scene(scene_path) else 120
    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd", "-c", "#1C1C1C"],
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
    rules_core_path = os.path.join(root, "generator", "prompts", "rules_core.md")
    with open(retry_system_path) as f:
        system = f.read()
    with open(rules_core_path) as f:
        rules = f.read()
    return system.strip() + "\n\n" + rules


def _write_attempt_artifacts(logs_dir: str, class_name: str, attempt: int, code: str, stderr: str) -> None:
    code_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.py")
    log_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.log")

    with open(code_path, "w") as f:
        f.write(code)
    with open(log_path, "w") as f:
        f.write(stderr)
