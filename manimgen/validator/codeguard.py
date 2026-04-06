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
    (r"\bscale_factor\s*=", "Remove `scale_factor`; FadeIn/FadeOut in ManimGL does not support it."),
    (r"\bCircumscribe\s*\(", "Use `FlashAround(...)` in ManimGL, not `Circumscribe(...)`."),
]

_BANNED_KWARGS = [
    "tip_length", "tip_width", "tip_shape", "corner_radius",
    "scale_factor", "target_position",
]


def apply_known_fixes(code: str) -> tuple[str, list[str]]:
    fixed = code
    applied: list[str] = []

    replacements = [
        (r"\bfrom\s+manim\s+import\s+\*", "from manimlib import *", "fixed import"),
        (r"\bMathTex\s*\(", "Tex(", "MathTex -> Tex"),
        (r"\bCreate\s*\(", "ShowCreation(", "Create -> ShowCreation"),
        (r"self\.camera\.frame", "self.frame", "self.camera.frame -> self.frame"),
        (r"\bCircumscribe\s*\(", "FlashAround(", "Circumscribe -> FlashAround"),
        (r"\bDARK_GREY\b", "GREY_D", "DARK_GREY -> GREY_D"),
        (r"\bDARK_GRAY\b", "GREY_D", "DARK_GRAY -> GREY_D"),
        (r"\bDARK_BLUE\b", "BLUE_D", "DARK_BLUE -> BLUE_D"),
        (r"\bDARK_GREEN\b", "GREEN_D", "DARK_GREEN -> GREEN_D"),
        (r"\bDARK_RED\b", "RED_D", "DARK_RED -> RED_D"),
        (r"\bLIGHT_GREY\b", "GREY_A", "LIGHT_GREY -> GREY_A"),
        (r"\bLIGHT_GRAY\b", "GREY_A", "LIGHT_GRAY -> GREY_A"),
    ]

    for pattern, repl, label in replacements:
        new_fixed, count = re.subn(pattern, repl, fixed)
        if count:
            applied.append(f"{label} ({count})")
            fixed = new_fixed

    for kw in _BANNED_KWARGS:
        new_fixed, count = re.subn(rf",?\s*{kw}\s*=\s*[^,\)\n]+", "", fixed)
        if count:
            applied.append(f"removed {kw} ({count})")
            fixed = new_fixed

    new_fixed, count = re.subn(
        r"Arrow\(\s*ORIGIN\s*,\s*ORIGIN(\s*[,)])",
        r"Arrow(ORIGIN, DOWN * 0.5\1",
        fixed,
    )
    if count:
        applied.append(f"fixed zero-length Arrow ({count})")
        fixed = new_fixed

    fixed, cast_applied = _fix_color_gradient_int_cast(fixed)
    if cast_applied:
        applied.append(cast_applied)

    fixed, font_applied = _remove_font_kwarg_from_tex(fixed)
    if font_applied:
        applied.append(font_applied)

    return fixed, applied


def _fix_color_gradient_int_cast(code: str) -> tuple[str, str | None]:
    """color_gradient(colors, length) — length must be int, not float.

    The first argument can be a list literal like [RED, BLUE], so we match
    either a bracketed expression or a plain identifier/literal as the colors arg.
    """
    # Match: color_gradient( <colors_arg> , <length_arg> )
    # colors_arg: either [...] or a plain non-paren token
    pattern = r"color_gradient\((\[[^\]]*\]|[^,)]+),\s*([^)]+)\)"

    def _replacer(m: re.Match) -> str:
        colors, length = m.group(1), m.group(2).strip()
        if length.startswith("int("):
            return m.group(0)  # already wrapped
        return f"color_gradient({colors}, int({length}))"

    new, count = re.subn(pattern, _replacer, code)
    if count:
        return new, f"color_gradient int cast ({count})"
    return code, None


def _remove_font_kwarg_from_tex(code: str) -> tuple[str, str | None]:
    """font= is only valid on Text(), never on Tex() or TexText()."""
    pattern = r"((?:Tex|TexText)\([^)]*?),?\s*font\s*=\s*[\"'][^\"']*[\"']([^)]*\))"
    new, count = re.subn(pattern, r"\1\2", code)
    if count:
        return new, f"removed font= from Tex ({count})"
    return code, None


def apply_error_aware_fixes(code: str, stderr: str) -> tuple[str, list[str]]:
    """Deterministic, token-free repairs driven by actual runtime traceback."""
    fixed = code
    applied: list[str] = []

    if "No such file or directory: 'latex'" in stderr or "latex: not found" in stderr:
        new_fixed, count = re.subn(r"\bTex\(\s*str\(([^)]+)\)\s*(,[^)]*)?\)", r"Text(str(\1)\2)", fixed)
        if count:
            applied.append(f"Tex(str(...)) -> Text(str(...)) ({count})")
            fixed = new_fixed

        new_fixed, count = re.subn(r"\bTex\(\s*f?\"([0-9\.\-]+)\"\s*(,[^)]*)?\)", r'Text("\1"\2)', fixed)
        if count:
            applied.append(f"Tex(numeric literal) -> Text(...) ({count})")
            fixed = new_fixed

    if "unexpected keyword argument" in stderr:
        kw_match = re.search(r"got an unexpected keyword argument '(\w+)'", stderr)
        if kw_match:
            bad_kw = kw_match.group(1)
            new_fixed, count = re.subn(rf",?\s*{bad_kw}\s*=\s*[^,\)\n]+", "", fixed)
            if count:
                applied.append(f"removed unexpected kwarg '{bad_kw}' ({count})")
                fixed = new_fixed

    if "NameError: name '" in stderr:
        name_match = re.search(r"NameError: name '(\w+)' is not defined", stderr)
        if name_match:
            bad_name = name_match.group(1)
            _name_fixes: dict[str, str] = {
                "DARK_GREY": "GREY_D", "DARK_GRAY": "GREY_D",
                "DARK_BLUE": "BLUE_D", "DARK_GREEN": "GREEN_D",
                "DARK_RED": "RED_D",
                "LIGHT_GREY": "GREY_A", "LIGHT_GRAY": "GREY_A",
                "LIGHT_BLUE": "BLUE_A", "LIGHT_GREEN": "GREEN_A",
                "LIGHT_RED": "RED_A",
                "DARK_BROWN": "GREY_D",
                "MAROON": "MAROON_B", "TEAL": "TEAL_C",
                "PURPLE": "PURPLE_B", "PINK": "PINK",
            }
            if bad_name in _name_fixes:
                fixed = fixed.replace(bad_name, _name_fixes[bad_name])
                applied.append(f"{bad_name} -> {_name_fixes[bad_name]} (error-aware)")

    if "TypeError" in stderr and "color_gradient" in stderr:
        fixed, cast_label = _fix_color_gradient_int_cast(fixed)
        if cast_label:
            applied.append(cast_label)

    if "TypeError" in stderr and ".animate" in stderr:
        lines = fixed.split("\n")
        new_lines: list[str] = []
        for line in lines:
            if ".animate" in line and ("FadeIn" in line or "FadeOut" in line) and "self.play" in line:
                indent = re.match(r"(\s*)", line).group(1)
                parts = re.findall(r"[^,]+\.animate\.[^,]+|FadeIn\([^)]+\)|FadeOut\([^)]+\)", line)
                if len(parts) >= 2:
                    for part in parts:
                        part = part.strip().rstrip(",").strip()
                        new_lines.append(f"{indent}self.play({part})")
                    applied.append("split mixed .animate + FadeIn/FadeOut")
                    continue
            new_lines.append(line)
        fixed = "\n".join(new_lines)

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
