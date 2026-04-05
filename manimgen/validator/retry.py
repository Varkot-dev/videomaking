import re
import subprocess
import os
from manimgen.llm import chat
from manimgen.validator.runner import _find_rendered_video
from manimgen.validator.codeguard import precheck_and_autofix, apply_error_aware_fixes
from manimgen.validator.env import get_render_env

MAX_RETRIES = 3


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


def retry_scene(section: dict, original_code: str, class_name: str, scene_path: str) -> tuple[bool, str | None]:
    """
    Retry generating and running a scene up to MAX_RETRIES times.
    Each retry sends the original code + error back to the LLM for a fix.
    Returns (success, video_path).
    """
    code = original_code
    logs_dir = "manimgen/output/logs"
    os.makedirs(logs_dir, exist_ok=True)
    system_prompt = _load_retry_system_prompt()

    for attempt in range(1, MAX_RETRIES + 1):
        result = _run_and_capture(scene_path, class_name)
        if result["success"]:
            return True, result["video_path"]

        error_type = _classify_error(result["stderr"])
        guidance = _fix_guidance(error_type)
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

        fixed = chat(
            system=system_prompt,
            user=f"""This ManimGL scene failed to render. Fix it.

Error type: {error_type}
Guidance: {guidance}

Full error:
{result['stderr']}

Original code:
{code}""",
        )

        if fixed.startswith("```"):
            fixed = re.sub(r"^```\w*\n?", "", fixed)
            fixed = re.sub(r"\n?```$", "", fixed)

        code = fixed
        with open(scene_path, "w") as f:
            f.write(code)

        # Local auto-fixes are free and often resolve common ManimGL mismatches.
        precheck = precheck_and_autofix(scene_path)
        if precheck["applied_fixes"]:
            with open(scene_path) as f:
                code = f.read()

    return False, None


def _run_and_capture(scene_path: str, class_name: str) -> dict:
    precheck = precheck_and_autofix(scene_path)
    if not precheck["ok"]:
        return {"success": False, "video_path": None, "stderr": precheck["stderr"]}

    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd"],
            capture_output=True,
            text=True,
            timeout=120,
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
    rules_core_path = os.path.join(root, "generator", "prompts", "rules_core.md")
    with open(rules_core_path) as f:
        rules = f.read()
    return (
        "You are a ManimGL scene repair agent.\n"
        "Goal: return corrected, runnable Python for ManimGL.\n"
        "Rules: output pure Python only, no markdown.\n\n"
        + rules
    )


def _write_attempt_artifacts(logs_dir: str, class_name: str, attempt: int, code: str, stderr: str) -> None:
    code_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.py")
    log_path = os.path.join(logs_dir, f"{class_name}_attempt{attempt}.log")

    with open(code_path, "w") as f:
        f.write(code)
    with open(log_path, "w") as f:
        f.write(stderr)
