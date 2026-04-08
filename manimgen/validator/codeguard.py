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
    (r"\.get_tex_string\s*\(", "Never call .get_tex_string() to read back values — store data in Python variables instead."),
    (r"\bscale_factor\s*=", "Remove `scale_factor`; FadeIn/FadeOut in ManimGL does not support it."),
    (r"\bCircumscribe\s*\(", "Use `FlashAround(...)` in ManimGL, not `Circumscribe(...)`."),
    (
        r"self\.play\(\s*(?:Surrounding|Background)Rectangle\s*\(",
        "Wrap SurroundingRectangle/BackgroundRectangle in ShowCreation(): "
        "self.play(ShowCreation(SurroundingRectangle(...))).",
    ),
    (
        r"""Tex\(\s*r?['"]\s*\\text\{[^}]*\}\s*['"]\s*[,)]""",
        r"Remove outer \text{} wrapper from Tex(): use Tex(r'content') not Tex(r'\text{content}'). "
        r"\text{} inside a longer expression like Tex(r'f(x) = \text{label}') is fine.",
    ),
    (
        r"\w+\[.+?\]\s*=(?!=)\s*\S",
        "VGroup does not support item assignment (vgroup[i] = x raises TypeError). "
        "Use a Python list for mutable indexed storage, then rebuild: "
        "items[i] = new_mob; group = VGroup(*items).",
    ),
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
        # SurroundingRectangle/BackgroundRectangle are Mobjects, not Animations.
        # Wrap them in ShowCreation() so self.play() can accept them.
        # This regex handles both types and also handles trailing play kwargs like run_time=.
        (
            r"self\.play\(\s*((Surrounding|Background)Rectangle\s*\([^)]*\))(\s*[,)])",
            r"self.play(ShowCreation(\1)\3",
            "wrapped SurroundingRectangle/BackgroundRectangle in ShowCreation",
        ),
        # NOTE: [^)]* matches up to the first ')' — works for single-level nesting like
        # SurroundingRectangle(VGroup(a, b)) but will mismatch if multiple nested calls
        # appear as separate positional args, e.g. SurroundingRectangle(func(a), func(b)).
        # That pattern is extremely rare in LLM-generated ManimGL code.
        # ManimCommunity Axes uses x_length/y_length; ManimGL uses width/height
        (r"\bx_length\s*=", "width=", "x_length -> width (ManimGL Axes)"),
        (r"\by_length\s*=", "height=", "y_length -> height (ManimGL Axes)"),
        (r"\.get_graph_point\s*\(", ".input_to_graph_point(", "get_graph_point -> input_to_graph_point"),
        (r"\._mobjects\b", ".submobjects", "_mobjects -> submobjects"),
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

    # ManimGL FadeIn/FadeOut take one mobject (+ optional kwargs). Models often
    # emit FadeOut(a, b) intending two animations. Rewrite to separate anims.
    new_fixed, count = re.subn(
        r"FadeOut\(\s*([\w\.]+)\s*,\s*([\w\.]+)\s*\)",
        r"FadeOut(\1), FadeOut(\2)",
        fixed,
    )
    if count:
        applied.append(f"split multi-arg FadeOut ({count})")
        fixed = new_fixed

    new_fixed, count = re.subn(
        r"FadeIn\(\s*([\w\.]+)\s*,\s*([\w\.]+)\s*\)",
        r"FadeIn(\1), FadeIn(\2)",
        fixed,
    )
    if count:
        applied.append(f"split multi-arg FadeIn ({count})")
        fixed = new_fixed

    # Replace unsupported curve.get_points_closer_to(target)[0][0] idiom with
    # a stable, deterministic approximation using sampled curve points.
    new_fixed, count = re.subn(
        r"(\w+)\.get_points_closer_to\(([^)]+)\)\[0\]\[0\]",
        r"\1.get_points()[len(\1.get_points()) // 2][0]",
        fixed,
    )
    if count:
        applied.append(f"get_points_closer_to -> sampled midpoint ({count})")
        fixed = new_fixed

    # CameraFrame API compatibility: some models emit set_x/y_values_from_bounds,
    # but this ManimGL build only supports set_width/set_height.
    new_fixed, count = re.subn(
        r"self\.frame\.set_x_values_from_bounds\(\s*([^,]+)\s*,\s*([^)]+)\s*\)",
        r"self.frame.set_width((\2) - (\1))",
        fixed,
    )
    if count:
        applied.append(f"frame x-bounds -> set_width ({count})")
        fixed = new_fixed

    new_fixed, count = re.subn(
        r"self\.frame\.set_y_values_from_bounds\(\s*([^,]+)\s*,\s*([^)]+)\s*\)",
        r"self.frame.set_height((\2) - (\1))",
        fixed,
    )
    if count:
        applied.append(f"frame y-bounds -> set_height ({count})")
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

    fixed, text_applied = _strip_outer_text_wrapper(fixed)
    if text_applied:
        applied.append(text_applied)

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


def _strip_outer_text_wrapper(code: str) -> tuple[str, str | None]:
    r"""Strip \text{...} when it is the sole content of a Tex() first argument.

    Matches:  Tex(r"\text{some label}")  or  Tex("\text{some label}")
    Leaves:   Tex(r"f(x) = \text{annotation}")  — \text mid-expression is valid.
    """
    # Match only when \text{...} is the ENTIRE string argument (anchored by
    # quote boundaries). Avoids touching valid mid-expression uses.
    pattern = re.compile(
        r"""(Tex\(\s*r?)(['"]) \s* \\text\{([^}]*)\} \s* \2""",
        re.VERBOSE,
    )
    new, count = re.subn(pattern, r"\1\2\3\2", code)
    if count:
        return new, f"stripped outer \\text{{}} wrapper from Tex() ({count})"
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
            if bad_kw == "font_size":
                # font_size is valid on Text() but not Tex()/MathTex(). Convert to
                # .scale() so sizing is preserved: Tex("x", font_size=48) -> Tex("x").scale(1.5)
                # 32 = baseline, scale = font_size / 32
                def _fs_to_scale(m: re.Match) -> str:
                    fs_val = m.group(1).strip()
                    try:
                        scale = round(float(fs_val) / 32, 2)
                    except ValueError:
                        scale = 1.0
                    return f").scale({scale})"
                new_fixed = re.sub(
                    r",?\s*font_size\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*(\))",
                    lambda m: f").scale({round(float(m.group(1)) / 32, 2)})",
                    fixed,
                )
                count = fixed != new_fixed
                if count:
                    applied.append(f"font_size= on Tex -> .scale() ({count})")
                    fixed = new_fixed
                else:
                    # Fallback: just strip it
                    new_fixed, count = re.subn(rf",?\s*{bad_kw}\s*=\s*[^,\)\n]+", "", fixed)
                    if count:
                        applied.append(f"removed unexpected kwarg '{bad_kw}' ({count})")
                        fixed = new_fixed
            else:
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
                # Common model mistake: this easing name is not present in ManimGL
                "slow_into_fast": "smooth",
            }
            if bad_name in _name_fixes:
                fixed = fixed.replace(bad_name, _name_fixes[bad_name])
                applied.append(f"{bad_name} -> {_name_fixes[bad_name]} (error-aware)")

    if "TypeError" in stderr and "color_gradient" in stderr:
        fixed, cast_label = _fix_color_gradient_int_cast(fixed)
        if cast_label:
            applied.append(cast_label)

    if "could not broadcast input array" in stderr:
        # Transform between mobjects with different point counts (e.g. Text("5") vs Text("11")).
        # Replace Transform(a, b) with FadeOut(a)/FadeIn(b) which doesn't require matching geometry.
        new_fixed, count = re.subn(
            r"\bTransform\(([^,]+),\s*([^)]+)\)",
            r"FadeOut(\1), FadeIn(\2)",
            fixed,
        )
        if count:
            applied.append(f"Transform -> FadeOut/FadeIn (point count mismatch) ({count})")
            fixed = new_fixed

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


def _check_next_to_stacking(lines: list[str], warnings: list[str]) -> None:
    """Warn when two .next_to(<same_anchor>, ...) calls appear within 6 lines.

    This is the primary cause of annotation labels stacking directly on top of
    each other (e.g. two labels both placed next_to a dashed line or axes).
    The fix is to group them in a VGroup and arrange/place once.
    """
    window = 6
    anchor_pattern = re.compile(r"\.next_to\(\s*(\w+)\s*,")
    for i, line in enumerate(lines):
        m = anchor_pattern.search(line)
        if not m:
            continue
        anchor = m.group(1)
        # look ahead within the window for another next_to with the same anchor
        for j in range(i + 1, min(i + window + 1, len(lines))):
            m2 = anchor_pattern.search(lines[j])
            if m2 and m2.group(1) == anchor:
                warnings.append(
                    f"Two .next_to({anchor}, ...) calls within {window} lines "
                    f"(lines {i+1} and {j+1}); labels will overlap. "
                    "Use VGroup(...).arrange(DOWN, buff=0.4) and place once."
                )
                break  # one warning per anchor is enough


def _check_layout_smells(code: str) -> list[str]:
    """Heuristic warnings for overlap-prone layout patterns."""
    warnings: list[str] = []
    if re.search(r"\bAxes\s*\(", code) and not re.search(r"\.set_width\s*\(|x_length\s*=|width\s*=|height\s*=", code):
        warnings.append(
            "Axes created without .set_width(); axes will render at default internal size, "
            "producing dead space or overflow. Use .set_width(10).center() "
            "(add .shift(DOWN * 0.5) if a title is present)."
        )
    # axes.move_to(ORIGIN) without set_width is a common dead-space cause
    if re.search(r"axes\.move_to\s*\(\s*ORIGIN\s*\)", code) and not re.search(r"\.set_width\s*\(", code):
        warnings.append(
            "axes.move_to(ORIGIN) used without .set_width(); this does not resize axes. "
            "Replace with axes.set_width(10).center() (or .center().shift(DOWN * 0.5) with a title)."
        )
    if re.search(r"\.move_to\s*\(\s*axes\.(?:c2p|i2gp|get_center)", code):
        warnings.append("Label moved into axes area; this often overlaps curves/ticks.")
    lines = code.strip().splitlines()
    tail = "\n".join(lines[-12:]) if lines else ""
    if "FadeOut" not in "\n".join(lines):
        warnings.append("Scene has no FadeOut at all — add one at the very end of the last cue.")
    # Detect multiple .next_to() calls sharing the same anchor within 6 lines — likely overlap
    _check_next_to_stacking(lines, warnings)
    # Axes tick font size: missing decimal_number_config causes oversized tick labels (default is 36)
    if re.search(r"\bAxes\s*\(", code):
        has_decimal_cfg = re.search(r"decimal_number_config", code)
        if not has_decimal_cfg:
            warnings.append(
                "Axes missing decimal_number_config in axis_config; tick labels will render at "
                "default font_size=36 (too large). Add "
                "decimal_number_config={\"font_size\": 24} inside axis_config."
            )
        # Also catch the common mistake of passing font_size directly (crashes at runtime)
        if re.search(r"axis_config\s*=\s*\{[^}]*[\"']font_size[\"']", code):
            warnings.append(
                "font_size passed directly in axis_config will crash (TypeError). "
                "Nest it inside decimal_number_config: "
                "axis_config={\"decimal_number_config\": {\"font_size\": 24}}."
            )
    return warnings


def precheck_and_autofix(code: str) -> str:
    """Apply all known auto-fixes to a code string and return the fixed code.

    Called by scene_generator before saving the file. Also called by retry.py
    on the file path (see precheck_and_autofix_file for that variant).
    """
    fixed, applied_fixes = apply_known_fixes(code)
    if applied_fixes:
        import logging
        logging.getLogger(__name__).debug("[codeguard] applied: %s", applied_fixes)
    return fixed


def precheck_and_autofix_file(scene_path: str) -> dict[str, Any]:
    """Read a scene file, apply auto-fixes, write back, return result dict."""
    with open(scene_path) as f:
        code = f.read()

    fixed = precheck_and_autofix(code)
    if fixed != code:
        with open(scene_path, "w") as f:
            f.write(fixed)

    errors = validate_scene_code(fixed)
    layout_warnings = _check_layout_smells(fixed)
    if errors:
        return {
            "ok": False,
            "stderr": "Precheck failed:\n- " + "\n- ".join(errors),
            "layout_warnings": layout_warnings,
        }

    return {
        "ok": True,
        "stderr": "",
        "layout_warnings": layout_warnings,
    }
