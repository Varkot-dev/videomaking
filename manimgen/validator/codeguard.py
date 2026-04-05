import ast
import re
from typing import Any


_BANNED_PATTERNS: list[tuple[str, str]] = [
    (r"\bfrom\s+manim\s+import\s+\*", "Use `from manimlib import *`, not `from manim import *`."),
    (r"\bMathTex\s*\(", "Use `Tex(...)` in ManimGL, not `MathTex(...)`."),
    (r"\bCreate\s*\(", "Use `ShowCreation(...)` in ManimGL, not `Create(...)`."),
    (r"\bself\.camera\.frame\b", "Use `self.frame`, not `self.camera.frame`."),
    (r"\btip_length\s*=", "Remove `tip_length`; ManimGL Arrow does not support it."),
    (r"\btip_width\s*=", "Remove `tip_width`; ManimGL Arrow does not support it."),
    (r"\btip_shape\s*=", "Remove `tip_shape`; ManimGL Arrow does not support it."),
    (r"\bcorner_radius\s*=", "Remove `corner_radius`; SurroundingRectangle does not support it."),
    (r"Arrow\(\s*ORIGIN\s*,\s*ORIGIN\s*[,)]", "Arrow start/end cannot be the same point."),
]


def apply_known_fixes(code: str) -> tuple[str, list[str]]:
    fixed = code
    applied: list[str] = []

    replacements = [
        (r"\bfrom\s+manim\s+import\s+\*", "from manimlib import *", "fixed import"),
        (r"\bMathTex\s*\(", "Tex(", "MathTex -> Tex"),
        (r"\bCreate\s*\(", "ShowCreation(", "Create -> ShowCreation"),
        (r"self\.camera\.frame", "self.frame", "self.camera.frame -> self.frame"),
        (r"\bDARK_GREY\b", "GREY_D", "DARK_GREY -> GREY_D"),
    ]

    for pattern, repl, label in replacements:
        new_fixed, count = re.subn(pattern, repl, fixed)
        if count:
            applied.append(f"{label} ({count})")
            fixed = new_fixed

    # Remove unsupported kwargs that repeatedly break Arrow/SurroundingRectangle in ManimGL.
    for kw in ["tip_length", "tip_width", "tip_shape", "corner_radius"]:
        new_fixed, count = re.subn(rf",?\s*{kw}\s*=\s*[^,\)\n]+", "", fixed)
        if count:
            applied.append(f"removed {kw} ({count})")
            fixed = new_fixed

    # Fix known zero-length Arrow pattern.
    new_fixed, count = re.subn(
        r"Arrow\(\s*ORIGIN\s*,\s*ORIGIN(\s*[,)])",
        r"Arrow(ORIGIN, DOWN * 0.5\1",
        fixed,
    )
    if count:
        applied.append(f"fixed zero-length Arrow ({count})")
        fixed = new_fixed

    return fixed, applied


def apply_error_aware_fixes(code: str, stderr: str) -> tuple[str, list[str]]:
    """
    Deterministic, token-free repairs driven by actual runtime traceback.
    """
    fixed = code
    applied: list[str] = []

    if "No such file or directory: 'latex'" in stderr:
        # Avoid LaTeX dependency for plain numeric labels.
        new_fixed, count = re.subn(r"\bTex\(\s*str\(([^)]+)\)\s*(,[^)]*)?\)", r"Text(str(\1)\2)", fixed)
        if count:
            applied.append(f"Tex(str(...)) -> Text(str(...)) ({count})")
            fixed = new_fixed

        new_fixed, count = re.subn(r"\bTex\(\s*f?\"([0-9\.\-]+)\"\s*(,[^)]*)?\)", r"Text(\"\1\"\2)", fixed)
        if count:
            applied.append(f"Tex(numeric literal) -> Text(...) ({count})")
            fixed = new_fixed

    if "NameError: name 'DARK_GREY' is not defined" in stderr and "DARK_GREY" in fixed:
        fixed = fixed.replace("DARK_GREY", "GREY_D")
        applied.append("DARK_GREY -> GREY_D (error-aware)")

    return fixed, applied


def validate_scene_code(code: str) -> list[str]:
    errors: list[str] = []

    try:
        ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"SyntaxError: {exc.msg} (line {exc.lineno})")

    for pattern, message in _BANNED_PATTERNS:
        if re.search(pattern, code):
            errors.append(message)

    return errors


def precheck_and_autofix(scene_path: str) -> dict[str, Any]:
    with open(scene_path) as f:
        code = f.read()

    fixed, applied_fixes = apply_known_fixes(code)
    if fixed != code:
        with open(scene_path, "w") as f:
            f.write(fixed)

    errors = validate_scene_code(fixed)
    if errors:
        return {
            "ok": False,
            "stderr": "Precheck failed:\n- " + "\n- ".join(errors),
            "applied_fixes": applied_fixes,
        }

    return {"ok": True, "stderr": "", "applied_fixes": applied_fixes}
