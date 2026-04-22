import ast
import re
from typing import Any

from manimgen.validator.invariants import run_all as _run_invariants


_CANONICAL_FONT_SIZES = (48, 44, 36, 28, 22, 20, 18)


def _snap_to_canonical_font_size(value: int) -> int:
    """Return the nearest canonical design-system font size."""
    return min(_CANONICAL_FONT_SIZES, key=lambda cs: abs(cs - value))


def _fix_font_size_to_scale(code: str) -> tuple[str, list[str]]:
    """Snap off-scale font_size literals to the nearest canonical value.

    Token-free enforcement of I4 (type scale). Cheaper than a retry, safer
    than trusting the Director to memorize the scale.
    """
    applied: list[str] = []

    def _replace(match: re.Match) -> str:
        value = int(match.group(1))
        if value in _CANONICAL_FONT_SIZES:
            return match.group(0)
        snapped = _snap_to_canonical_font_size(value)
        applied.append(f"font_size={value} -> {snapped}")
        return f"font_size={snapped}"

    new = re.sub(r"\bfont_size\s*=\s*(\d+)", _replace, code)
    return new, applied


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
    (r"\.get_tex_string\s*\(", "Never call .get_tex_string() to read back values — store data in a plain Python list instead (e.g. current_values = [5, 3, 8, 1]) and compare current_values[i] > current_values[j]. Never read values back from Tex/Text mobjects."),
    (r"\.set_fill_color\s*\(", "Use .set_fill(color) not .set_fill_color(). ManimGL: obj.set_fill(RED, opacity=1)."),
    (r"\.set_text\s*\(", "Text has no set_text() method in ManimGL. To update a counter label: create a new Text(...) and use FadeOut(old), FadeIn(new) or ReplacementTransform(old, new)."),
    (r"\bscale_factor\s*=", "Remove `scale_factor`; FadeIn/FadeOut in ManimGL does not support it."),
    (r"\bCircumscribe\s*\(", "Use `FlashAround(...)` in ManimGL, not `Circumscribe(...)`."),
    (
        r"self\.frame\.\s*set_light",
        "self.frame has no set_light method. Light is on self.camera: "
        "`light = self.camera.light_source; self.play(light.animate.move_to(pos))`.",
    ),
    (
        r"self\.frame\.\s*set_euler_angles",
        "Use self.frame.reorient(theta_deg, phi_deg) — not set_euler_angles().",
    ),
    (
        r"add_fixed_in_frame_mobjects\s*\(",
        "add_fixed_in_frame_mobjects() does not exist. Use label.fix_in_frame() on each mobject.",
    ),
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
        # Tuple-swap on VGroup-style names: boxes[i], boxes[j] = boxes[j], boxes[i]
        # Only matches known VGroup-style plural names. Excludes _list suffix (safe parallel lists).
        r"\b(boxes|labels|cells|group|vgroup|mobs|mobjects|elems|elements|shapes|squares|circles|arrows)\b(?!_list)"
        r"\[.+?\]\s*,\s*"
        r"(boxes|labels|cells|group|vgroup|mobs|mobjects|elems|elements|shapes|squares|circles|arrows)\b(?!_list)"
        r"\[.+?\]\s*=(?!=)",
        "VGroup does not support item assignment. "
        "Use a parallel Python list: box_list = list(boxes), then swap box_list[i], box_list[j]. "
        "Never assign into the VGroup directly.",
    ),
    (
        # Single-assign on VGroup-style name: boxes[i] = new_mob
        # Excludes _list suffix names.
        r"\b(boxes|labels|cells|group|vgroup|mobs|mobjects|elems|elements|shapes|squares|circles|arrows)\b(?!_list)"
        r"\[.+?\]\s*=(?!=)\s*\S",
        "VGroup does not support item assignment. "
        "Use a parallel Python list: box_list = list(boxes). "
        "Never assign into the VGroup directly.",
    ),
    (
        r"\bself\.set_camera_orientation\s*\(",
        "set_camera_orientation() is ManimCommunity — it does not exist in ManimGL. "
        "Use self.frame.reorient(theta_degrees, phi_degrees) inside a ThreeDScene, "
        "or remove the call entirely for 2D scenes.",
    ),
    (
        r"\.reorient\(\s*(?:theta_deg|phi_deg)\s*=",
        "reorient() uses theta_degrees= and phi_degrees= (not theta_deg/phi_deg). "
        "Or call positionally: self.frame.reorient(theta_val, phi_val).",
    ),
    (
        r"\.get_parts_by_tex_expression\s*\(",
        "get_parts_by_tex_expression() does not exist on Tex in ManimGL. "
        "To highlight a sub-expression, use a separate Tex() object positioned with .move_to() "
        "or .next_to(), or use get_part_by_tex(r'\\symbol') if the symbol is a single token.",
    ),
]

_BANNED_KWARGS = [
    "tip_length", "tip_width", "tip_shape", "corner_radius",
    "scale_factor", "target_position",
]

# Registry of known-wrong kwarg names per method.
# Maps method_name → {wrong_kwarg: correct_kwarg or None (strip)}.
# Used by both apply_known_fixes (proactive) and apply_error_aware_fixes (reactive).
_KWARG_NORMALIZATION_REGISTRY: dict[str, dict[str, str | None]] = {
    "arrange_in_grid": {
        "rows":     "n_rows",
        "cols":     "n_cols",
        "row_buff": "buff",
        "col_buff": "buff",
    },
    "reorient": {
        "theta_deg": "theta_degrees",
        "phi_deg":   "phi_degrees",
    },
    "NumberLine": {
        "label": None,
    },
}


def _fix_arrange_in_grid_kwargs(code: str) -> tuple[str, str | None]:
    """Normalize all wrong kwarg names on .arrange_in_grid() calls in one pass.

    Correct signature: arrange_in_grid(n_rows=None, n_cols=None, buff=MED_SMALL_BUFF)
    LLM commonly emits rows=, cols=, row_buff=, col_buff= simultaneously.
    """
    norm = _KWARG_NORMALIZATION_REGISTRY["arrange_in_grid"]
    applied = []
    fixed = code
    for wrong, right in norm.items():
        if right is None:
            new, count = re.subn(
                rf"(\.arrange_in_grid\([^)]*?),?\s*{re.escape(wrong)}\s*=\s*[^,\)\n]+",
                r"\1",
                fixed,
                flags=re.DOTALL,
            )
        else:
            new, count = re.subn(
                rf"(\.arrange_in_grid\([^)]*?)\b{re.escape(wrong)}\s*=",
                rf"\1{right}=",
                fixed,
                flags=re.DOTALL,
            )
        if count:
            applied.append(f"{wrong}= → {right or 'stripped'} ({count})")
            fixed = new
    if applied:
        return fixed, "fixed arrange_in_grid kwargs: " + ", ".join(applied)
    return code, None


def apply_known_fixes(code: str) -> tuple[str, list[str]]:
    fixed = code
    applied: list[str] = []

    replacements = [
        (r"\bfrom\s+manim\s+import\s+\*", "from manimlib import *", "fixed import"),
        (r"\bMathTex\s*\(", "Tex(", "MathTex -> Tex"),
        (r"\bCreate\s*\(", "ShowCreation(", "Create -> ShowCreation"),
        (r"self\.camera\.frame", "self.frame", "self.camera.frame -> self.frame"),
        (r"\bCircumscribe\s*\(", "FlashAround(", "Circumscribe -> FlashAround"),
        # ManimCommunity Axes uses x_length/y_length; ManimGL uses width/height
        (r"\bx_length\s*=", "width=", "x_length -> width (ManimGL Axes)"),
        (r"\by_length\s*=", "height=", "y_length -> height (ManimGL Axes)"),
        (r"\.get_graph_point\s*\(", ".input_to_graph_point(", "get_graph_point -> input_to_graph_point"),
        (r"\._mobjects\b", ".submobjects", "_mobjects -> submobjects"),
        (r"\.set_fill_color\s*\(", ".set_fill(", "set_fill_color -> set_fill"),
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

    # Palette role hex → MaminGL constant (I3). Maps the canonical hex values from
    # COLOR_PALETTE.md (CANONICAL aesthetic) to their ManimGL constant equivalents.
    _HEX_TO_CONSTANT: list[tuple[str, str, str]] = [
        (r'"#00D9FF"', "TEAL_A",   "PRIMARY hex -> TEAL_A"),
        (r'"#FF6B35"', "GOLD",     "SECONDARY hex -> GOLD"),
        (r'"#3DD17B"', "GREEN",    "SUCCESS hex -> GREEN"),
        (r'"#FFC857"', "YELLOW",   "WARNING hex -> YELLOW"),
        (r'"#E5484D"', "RED",      "ALERT hex -> RED"),
        (r'"#E8E8E8"', "WHITE",    "INK hex -> WHITE"),
        (r'"#9A9A9A"', "GREY_A",   "MUTED hex -> GREY_A"),
        (r'"#4A4A4A"', "GREY_D",   "SUBTLE hex -> GREY_D"),
        (r'"#3A6F8A"', "GREY_B",   "STRUCT hex -> GREY_B"),
        (r'"#58C4DD"', "TEAL_B",   "legacy TEAL hex -> TEAL_B"),
        (r'"#1C1C1C"', "GREY_E",   "dark bg hex -> GREY_E"),
    ]
    for hex_pat, const, label in _HEX_TO_CONSTANT:
        new_fixed, count = re.subn(hex_pat, const, fixed)
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

    fixed, rect_applied = _wrap_bare_rect_in_show_creation(fixed)
    if rect_applied:
        applied.append(rect_applied)

    fixed, tmt_applied = _fix_transform_matching_tex_on_text(fixed)
    if tmt_applied:
        applied.append(tmt_applied)

    fixed, become_applied = _fix_become_inside_play(fixed)
    if become_applied:
        applied.append(become_applied)

    fixed, cam_applied = _fix_set_camera_orientation(fixed)
    if cam_applied:
        applied.append(cam_applied)

    fixed, reorient_applied = _fix_reorient_wrong_kwargs(fixed)
    if reorient_applied:
        applied.append(reorient_applied)

    fixed, numberline_applied = _strip_label_kwarg_from_numberline(fixed)
    if numberline_applied:
        applied.append(numberline_applied)

    fixed, grid_applied = _fix_arrange_in_grid_kwargs(fixed)
    if grid_applied:
        applied.append(grid_applied)

    fixed, yaxis_applied = _fix_y_axis_include_numbers(fixed)
    if yaxis_applied:
        applied.append(yaxis_applied)

    new_fixed, count = re.subn(
        r"self\.wait\(\s*(?:-\s*[\d.]+|0+\.0+|(?<!\d)0(?![\d.]))\s*\)",
        "self.wait(0.01)",
        fixed,
    )
    if count:
        applied.append(f"clamped negative/zero self.wait() to 0.01 ({count})")
        fixed = new_fixed

    fixed, font_fixes = _fix_font_size_to_scale(fixed)
    for fix in font_fixes:
        applied.append(fix)

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


def _wrap_bare_rect_in_show_creation(code: str) -> tuple[str, str | None]:
    """Wrap bare SurroundingRectangle/BackgroundRectangle in ShowCreation().

    self.play(SurroundingRectangle(obj, color=YELLOW))
      → self.play(ShowCreation(SurroundingRectangle(obj, color=YELLOW)))

    Uses depth-aware paren matching so nested args like
    SurroundingRectangle(Text("hello"), color=YELLOW) are handled correctly.
    """
    rect_start_re = re.compile(
        r"self\.play\(\s*((Surrounding|Background)Rectangle)\s*\("
    )
    result_parts: list[str] = []
    pos = 0
    count = 0
    while pos < len(code):
        m = rect_start_re.search(code, pos)
        if not m:
            result_parts.append(code[pos:])
            break

        # Check if already wrapped in ShowCreation/FadeIn/Write
        prefix = code[m.start(0):m.start(1)]
        # m.start(0) is "self.play(", m.start(1) is the rect name
        # Capture text between "self.play(" and the rect name
        between = code[m.start(0) + len("self.play("):m.start(1)].strip()
        if between:
            # There's something already there (e.g. ShowCreation()
            result_parts.append(code[pos:m.end(0)])
            pos = m.end(0)
            continue

        # Walk depth-aware from the opening paren of Rectangle(
        rect_open = m.end(0) - 1  # index of '(' after Rectangle name
        depth = 1
        i = rect_open + 1
        while i < len(code) and depth > 0:
            if code[i] == '(':
                depth += 1
            elif code[i] == ')':
                depth -= 1
            i += 1
        rect_close = i - 1  # index of the matching ')'

        # The full rect call including its closing paren
        rect_call = code[m.start(1):rect_close + 1]

        # What comes after the rect call: should be ')' to close self.play()
        # or ', run_time=...' etc. — we keep those unchanged
        after_rect = code[rect_close + 1:]

        result_parts.append(code[pos:m.start(1)])
        result_parts.append(f"ShowCreation({rect_call})")
        pos = rect_close + 1
        count += 1

    new_code = "".join(result_parts)
    if count:
        return new_code, f"wrapped bare SurroundingRectangle/BackgroundRectangle in ShowCreation ({count})"
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


def _detect_tmt_on_text(code: str) -> list[str]:
    """Return error strings if TransformMatchingTex is used on Text() variables.

    TransformMatchingTex matches LaTeX glyph submobjects. On Text() objects it
    produces scrambled animation output (not a crash, but visually broken).
    """
    text_var_re = re.compile(
        r"\b(\w+)\s*=\s*(?:always_redraw\(\s*lambda[^:]*:\s*)?Text\s*\("
    )
    text_vars: set[str] = set(text_var_re.findall(code))
    if not text_vars:
        return []
    tmt_re = re.compile(r"TransformMatchingTex\(\s*(\w+)\s*,")
    errors = []
    for m in tmt_re.finditer(code):
        a = m.group(1)
        if a in text_vars:
            errors.append(
                f"TransformMatchingTex({a}, ...) — '{a}' is a Text() object. "
                "TransformMatchingTex only works on Tex() objects (LaTeX glyph matching). "
                "Use FadeOut(a), FadeIn(b) for Text() counter/label updates."
            )
    return errors


def _fix_transform_matching_tex_on_text(code: str) -> tuple[str, str | None]:
    """Auto-convert TransformMatchingTex(Text_var, ...) to FadeOut/FadeIn.

    Collects variable names assigned via Text(...), then rewrites any
    TransformMatchingTex(a, b, ...) where a is a Text variable to
    FadeOut(a, run_time=X), FadeIn(b, run_time=X) preserving run_time= if present.
    Does NOT touch TransformMatchingTex where the first arg is a Tex() variable.
    """
    text_var_re = re.compile(
        r"\b(\w+)\s*=\s*(?:always_redraw\(\s*lambda[^:]*:\s*)?Text\s*\("
    )
    text_vars: set[str] = set(text_var_re.findall(code))
    if not text_vars:
        return code, None

    tmt_re = re.compile(
        r"TransformMatchingTex\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*([^)]*))?\)"
    )
    count = 0

    def _replacer(m: re.Match) -> str:
        nonlocal count
        a, b = m.group(1), m.group(2)
        extra = m.group(3) or ""
        if a not in text_vars:
            return m.group(0)
        rt_match = re.search(r"run_time\s*=\s*[\d.]+", extra)
        rt = f", {rt_match.group(0)}" if rt_match else ""
        count += 1
        return f"FadeOut({a}{rt}), FadeIn({b}{rt})"

    result = tmt_re.sub(_replacer, code)
    if count:
        return result, f"TransformMatchingTex(Text, ...) -> FadeOut/FadeIn ({count})"
    return code, None


def _fix_become_inside_play(code: str) -> tuple[str, str | None]:
    """Rewrite self.play(obj.become(...), ...) to the correct two-step form.

    In ManimGL, become() returns self (the mutated Mobject), not an Animation.
    Passing it to self.play() is equivalent to self.play(obj) which crashes with
    "Object X cannot be converted to an animation".

    Correct pattern:
        obj.become(SurroundingRectangle(...))
        self.play(ShowCreation(obj), run_time=X)

    Handles both single-line and multiline self.play() forms:
        # single-line:
        self.play(scan_rect.become(SurroundingRectangle(...)), run_time=0.2)
        # multiline:
        self.play(
            scan_rect.become(SurroundingRectangle(...)),
            run_time=0.2
        )
    """
    # Find self.play( ... var.become( ... ) ... ) using depth-aware scan on the full code.
    # We do NOT use .animate.become() — that is a different (valid) pattern.
    play_re = re.compile(r"([ \t]*)self\.play\(")
    result_parts: list[str] = []
    pos = 0
    count = 0

    while pos < len(code):
        m = play_re.search(code, pos)
        if not m:
            result_parts.append(code[pos:])
            break

        indent = m.group(1)
        play_open = m.end() - 1  # index of '(' in self.play(

        # Walk the full self.play(...) call depth-aware
        depth = 1
        i = play_open + 1
        while i < len(code) and depth > 0:
            if code[i] == '(':
                depth += 1
            elif code[i] == ')':
                depth -= 1
            i += 1
        play_close = i - 1  # index of the closing ) of self.play(...)

        play_interior = code[play_open + 1:play_close]  # everything inside self.play(...)

        # Check if interior contains var.become( but NOT var.animate.become(
        become_re = re.compile(r"(?<!\.)(?<!animate\.)(\w+)\.become\(")
        bm = become_re.search(play_interior)

        if not bm:
            # No bare .become() — leave unchanged
            result_parts.append(code[pos:play_close + 1])
            pos = play_close + 1
            continue

        var_name = bm.group(1)

        # Extract the content of var.become(...) using depth-aware scan inside play_interior
        become_open_in_interior = bm.end() - 1  # '(' of become(
        bdepth = 1
        bi = become_open_in_interior + 1
        while bi < len(play_interior) and bdepth > 0:
            if play_interior[bi] == '(':
                bdepth += 1
            elif play_interior[bi] == ')':
                bdepth -= 1
            bi += 1
        become_close_in_interior = bi - 1
        become_inner = play_interior[become_open_in_interior + 1:become_close_in_interior]

        # Extract run_time kwarg from the play_interior (after the become call)
        after_become = play_interior[become_close_in_interior + 1:]
        rt_match = re.search(r"run_time\s*=\s*[\d.]+", after_become)
        rt_str = f", {rt_match.group(0)}" if rt_match else ""

        # Emit: become() on its own line, then self.play(ShowCreation(...))
        result_parts.append(code[pos:m.start()])  # code before this self.play
        result_parts.append(f"{indent}{var_name}.become({become_inner})\n")
        result_parts.append(f"{indent}self.play(ShowCreation({var_name}){rt_str})")
        pos = play_close + 1
        count += 1

    if count:
        return "".join(result_parts), f"self.play(obj.become(...)) -> become()+ShowCreation ({count})"
    return code, None


def _fix_set_camera_orientation(code: str) -> tuple[str, str | None]:
    """Rewrite ManimCommunity set_camera_orientation() → self.frame.reorient().

    Handles the most common form:
        self.set_camera_orientation(phi=60 * DEGREES, theta=-45 * DEGREES)
      → self.frame.reorient(-45, 60)

    phi and theta may appear in either order and with optional `* DEGREES` suffix.
    Values without `* DEGREES` are passed through as-is (assumed already in degrees).

    If the call cannot be parsed cleanly, the entire line is removed to prevent
    AttributeError crashes — the retry LLM will receive the banned-pattern message.
    """
    outer = re.compile(r"self\.set_camera_orientation\(([^)]*)\)")

    def _replacer(m: re.Match) -> str:
        args = m.group(1)
        phi_m = re.search(r"\bphi\s*=\s*(-?[\d.]+)\s*(?:\*\s*DEGREES)?", args)
        theta_m = re.search(r"\btheta\s*=\s*(-?[\d.]+)\s*(?:\*\s*DEGREES)?", args)
        if phi_m and theta_m:
            phi_val = phi_m.group(1)
            theta_val = theta_m.group(1)
            return f"self.frame.reorient({theta_val}, {phi_val})"
        return "pass  # removed unparseable set_camera_orientation call"

    new, count = re.subn(outer, _replacer, code)
    if count:
        return new, f"set_camera_orientation -> self.frame.reorient ({count})"
    return code, None


def _fix_reorient_wrong_kwargs(code: str) -> tuple[str, str | None]:
    """Fix wrong kwarg names on self.frame.reorient() calls.

    The Director sometimes emits:
        self.frame.reorient(theta_deg=-45, phi_deg=60)

    The real param names are theta_degrees= and phi_degrees= (or positional).
    """
    applied = []
    fixed = code

    for wrong, right in [("theta_deg=", "theta_degrees="), ("phi_deg=", "phi_degrees=")]:
        new, count = re.subn(re.escape(wrong), right, fixed)
        if count:
            applied.append(f"{wrong} -> {right} ({count})")
            fixed = new

    if applied:
        return fixed, "fixed reorient kwarg names: " + ", ".join(applied)
    return code, None


def _strip_label_kwarg_from_numberline(code: str) -> tuple[str, str | None]:
    """Strip label= kwarg from NumberLine() — not a valid ManimGL parameter."""
    new, count = re.subn(r"(NumberLine\([^)]*?),?\s*label\s*=\s*[^,\)]+", r"\1", code)
    if count:
        return new, f"removed label= from NumberLine ({count})"
    return code, None


def _fix_broken_call_args(code: str) -> tuple[str, list[str]]:
    """Fix LLM-generated calls with leading/trailing commas in argument lists.

    Patterns like get_axis_labels(, y_label=...) and reorient(, theta=...)
    are SyntaxErrors. Strip the stray leading comma.
    """
    applied: list[str] = []
    # Leading comma after open paren: func(, arg) → func(arg)
    new, count = re.subn(r"\(\s*,\s*", "(", code)
    if count:
        applied.append(f"removed leading comma in call args ({count})")
        code = new
    # Trailing comma before close paren when nothing follows: func(arg,) → func(arg)
    new, count = re.subn(r",\s*\)", ")", code)
    if count:
        applied.append(f"removed trailing comma in call args ({count})")
        code = new
    # reorient(, theta=X * DEGREES) → reorient() — can't salvage partial 3D args
    new, count = re.subn(r"\.reorient\(\s*,\s*theta\s*=\s*[^)]+\)", ".reorient(-45, 70)", code)
    if count:
        applied.append(f"fixed broken reorient call ({count})")
        code = new
    return code, applied


def apply_error_aware_fixes(code: str, stderr: str) -> tuple[str, list[str]]:
    """Deterministic, token-free repairs driven by actual runtime traceback."""
    fixed = code
    applied: list[str] = []

    # Always run structural syntax fixers regardless of error type
    fixed, structural_fixes = _fix_broken_call_args(fixed)
    applied.extend(structural_fixes)

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
        hint_match = re.search(r"Did you mean '(\w+)'\?", stderr)
        method_match = re.search(r"(\w+)\(\) got an unexpected keyword argument", stderr)
        if kw_match:
            bad_kw = kw_match.group(1)
            method = method_match.group(1) if method_match else None
            # font_size= is a valid kwarg on Tex() (handled internally) — do not strip or convert.
            if bad_kw == "font_size":
                pass
            elif method and method in _KWARG_NORMALIZATION_REGISTRY:
                # Fix ALL known wrong kwargs for this method in one pass — not just the one
                # named in the error. This prevents the one-kwarg-at-a-time peeling pattern.
                norm = _KWARG_NORMALIZATION_REGISTRY[method]
                for wrong, right in norm.items():
                    if right is None:
                        new_fixed, count = re.subn(
                            rf",?\s*{re.escape(wrong)}\s*=\s*[^,\)\n]+", "", fixed
                        )
                    else:
                        new_fixed, count = re.subn(
                            rf"\b{re.escape(wrong)}\s*=", f"{right}=", fixed
                        )
                    if count:
                        applied.append(f"registry fix: {wrong}= → {right or 'stripped'} ({count})")
                        fixed = new_fixed
            elif hint_match:
                # Rename, don't strip — the traceback tells us what the right name is.
                good_kw = hint_match.group(1)
                new_fixed, count = re.subn(
                    rf"\b{re.escape(bad_kw)}\s*=",
                    f"{good_kw}=",
                    fixed,
                )
                if count:
                    applied.append(f"renamed kwarg '{bad_kw}' → '{good_kw}' ({count})")
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
    """Return errors that must block the render.

    Syntax + banned patterns + TMT-on-Text + design-system ERROR invariants
    (via the invariants registry). Warnings go through run_invariant_warnings.
    """
    errors: list[str] = []

    try:
        ast.parse(code)
    except SyntaxError as exc:
        errors.append(f"SyntaxError: {exc.msg} (line {exc.lineno})")

    for pattern, message in _BANNED_PATTERNS:
        if re.search(pattern, code):
            errors.append(message)

    errors.extend(_detect_tmt_on_text(code))

    inv_errors, _ = _run_invariants(code)
    errors.extend(inv_errors)

    return errors


def run_invariant_warnings(code: str) -> list[str]:
    """Return the design-system WARNING invariants for a code string.

    Surfaced to the retry LLM via precheck_and_autofix_file's layout_warnings
    channel. Does not block the render.
    """
    _, warnings = _run_invariants(code)
    return warnings


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


def _check_loop_timing_smells(code: str) -> list[str]:
    """Warn when self.wait() follows a for/while loop body with no timing accumulator.

    Pattern that causes A/V mismatch: the Director computes total loop run_time as
    `n * per_iter_time` but only subtracts `per_iter_time` in the wait. The correct
    pattern is to accumulate loop run_times into any variable inside the loop body,
    then reference that variable in the subsequent self.wait() call.

    Heuristic (semantic, not name-based):
      1. Find every for/while block containing a self.play(..., run_time=...) call.
      2. Collect any variable names accumulated with += inside the loop body.
      3. Scan ahead for the next statement after the loop — stopping at any self.play().
         If the next statement is a self.wait() and none of the accumulated variables
         appear inside that wait's argument, the timing is unaccounted for.
      → emit a structured warning.

    This avoids two failure modes:
      - False positive: self.play() between loop and wait means timing IS accounted for.
      - False negative: accumulator named anything other than a hardcoded list.
    """
    warnings: list[str] = []
    lines = code.splitlines()

    loop_header_re = re.compile(r"^(\s*)(for |while )")
    play_with_runtime_re = re.compile(r"self\.play\(.*run_time\s*=")
    augmented_assign_re = re.compile(r"\b(\w+)\s*\+=")
    wait_re = re.compile(r"self\.wait\s*\(")
    play_re = re.compile(r"self\.play\s*\(")

    i = 0
    while i < len(lines):
        header_m = loop_header_re.match(lines[i])
        if not header_m:
            i += 1
            continue

        loop_indent = header_m.group(1)
        header_indent_len = len(loop_indent)

        # Collect loop body: lines indented deeper than the loop header
        j = i + 1
        while j < len(lines):
            if lines[j].strip() == "":
                j += 1
                continue
            if len(lines[j]) - len(lines[j].lstrip()) <= header_indent_len:
                break
            j += 1
        loop_end = j

        body_text = "\n".join(lines[i + 1:loop_end])

        if not play_with_runtime_re.search(body_text):
            i = loop_end if loop_end > i else i + 1
            continue

        # Variables accumulated with += anywhere inside the loop body
        accumulated_vars: set[str] = set(augmented_assign_re.findall(body_text))

        # Scan ahead: find the next non-empty, non-comment statement after loop_end.
        # Stop immediately if we hit a self.play() — timing is handled there.
        k = loop_end
        while k < len(lines):
            la = lines[k].strip()
            if not la or la.startswith("#"):
                k += 1
                continue
            if play_re.search(la):
                # Intervening play() — cannot conclude timing is wrong
                break
            if wait_re.search(la):
                # wait() found — check if any accumulated var appears as a whole
                # identifier in the wait argument (word-boundary match, not substring).
                timing_accounted = any(
                    re.search(rf"\b{re.escape(v)}\b", la)
                    for v in accumulated_vars
                )
                if not timing_accounted:
                    line_num = k + 1  # 1-indexed
                    warnings.append(
                        f"Loop timing: self.wait() after loop at line ~{line_num} — "
                        "accumulate run_times inside the loop into any variable "
                        "(e.g. `anim_time += <run_time>`), then use "
                        "`self.wait(max(0.01, cue_dur - anim_time))`."
                    )
                break
            # Any other statement (assignment, etc.) — keep scanning
            k += 1

        i = loop_end if loop_end > i else i + 1

    return warnings


def _check_horizontal_chain_overflow(lines: list[str], warnings: list[str]) -> None:
    """Warn when 3+ objects are chained horizontally with .next_to(..., RIGHT).

    Horizontal chains like:
        eq1.next_to(title, DOWN)
        eq2.next_to(eq1, RIGHT)
        eq3.next_to(eq2, RIGHT)
    accumulate x-position and overflow past x=7. Equation derivation steps
    should stack vertically (DOWN), not horizontally (RIGHT).
    """
    next_to_right_re = re.compile(r"\.next_to\(\s*(\w+)\s*,\s*RIGHT")
    # Track chains: for each object, record what it is placed right-of
    right_of: dict[str, str] = {}  # variable -> anchor
    assignment_re = re.compile(r"(\w+)\s*=\s*.*\.next_to\(\s*(\w+)\s*,\s*RIGHT")

    for line in lines:
        m = assignment_re.search(line)
        if m:
            var_name, anchor = m.group(1), m.group(2)
            right_of[var_name] = anchor

    # Find chains of length >= 3
    for var in right_of:
        chain = [var]
        current = var
        while current in right_of:
            current = right_of[current]
            chain.append(current)
        if len(chain) >= 3:
            chain_str = " → ".join(reversed(chain))
            warnings.append(
                f"Horizontal chain detected ({chain_str}): {len(chain)} objects chained with "
                ".next_to(..., RIGHT). This will overflow past the right screen edge (x > 7). "
                "Stack equation derivation steps vertically with .next_to(prev, DOWN, buff=0.3) "
                "instead of horizontally."
            )
            break  # one warning per scene is enough


def _check_layout_smells(code: str) -> list[str]:
    """Codeguard-local layout heuristics that are not design-system invariants.

    Design-system invariants (I2/I3/I4/I5/I7/I9) live in invariants.py and are
    surfaced via run_invariant_warnings. This function covers mechanical ManimGL
    traps: axes sizing, axes tick font, stacking on shared anchors, horizontal
    overflow chains. These are local to codeguard because they relate to
    specific ManimGL API pitfalls, not to abstract design rules.
    """
    warnings: list[str] = []
    if re.search(r"\bAxes\s*\(", code) and not re.search(r"\.set_width\s*\(|x_length\s*=|width\s*=|height\s*=", code):
        warnings.append(
            "Axes created without .set_width(); axes will render at default internal size, "
            "producing dead space or overflow. Use .set_width(10).center() "
            "(add .shift(DOWN * 0.5) if a title is present)."
        )
    if re.search(r"axes\.move_to\s*\(\s*ORIGIN\s*\)", code) and not re.search(r"\.set_width\s*\(", code):
        warnings.append(
            "axes.move_to(ORIGIN) used without .set_width(); this does not resize axes. "
            "Replace with axes.set_width(10).center() (or .center().shift(DOWN * 0.5) with a title)."
        )
    if re.search(r"\.move_to\s*\(\s*axes\.(?:c2p|i2gp|get_center)", code):
        warnings.append("Label moved into axes area; this often overlaps curves/ticks.")

    lines = code.strip().splitlines()
    _check_next_to_stacking(lines, warnings)
    _check_horizontal_chain_overflow(lines, warnings)

    right_anchor_re = re.compile(
        r"\.next_to\(\s*(parabola|axes|graph|curve|surface|table_headers)\s*,\s*RIGHT"
    )
    if right_anchor_re.search(code):
        warnings.append(
            "Content placed .next_to(axes/parabola/graph, RIGHT) will likely "
            "overflow past the right screen edge. Place it below or to the left instead, "
            "or use .to_edge(RIGHT) with a buff."
        )

    if re.search(r"\bAxes\s*\(", code):
        if not re.search(r"decimal_number_config", code):
            warnings.append(
                "Axes missing decimal_number_config in axis_config; tick labels will render at "
                "default font_size=36 (too large). Add "
                "decimal_number_config={\"font_size\": 24} inside axis_config."
            )
        if re.search(r"axis_config\s*=\s*\{[^}]*[\"']font_size[\"']", code):
            warnings.append(
                "font_size passed directly in axis_config will crash (TypeError). "
                "Nest it inside decimal_number_config: "
                "axis_config={\"decimal_number_config\": {\"font_size\": 24}}."
            )
    return warnings


def _fix_y_axis_include_numbers(code: str) -> tuple[str, str | None]:
    """Force y_axis_config include_numbers to False.

    ManimGL rotates y-axis number labels 90° and stacks them when
    include_numbers=True, making them crash into each other and become
    unreadable. Always disable and use manual Text labels instead.
    """
    pattern = re.compile(
        r'(y_axis_config\s*=\s*\{[^}]*)"include_numbers"\s*:\s*True([^}]*\})'
    )
    new, count = re.subn(pattern, r'\1"include_numbers": False\2', code)
    if count:
        return new, f'forced y_axis include_numbers=False ({count})'
    return code, None


def precheck_and_autofix(code: str) -> str:
    """Apply all known auto-fixes to a code string and return the fixed code.

    Called by scene_generator before saving the file. Also called by retry.py
    on the file path (see precheck_and_autofix_file for that variant).
    """
    # Fix structural syntax errors first (leading/trailing commas in calls)
    fixed, structural_fixes = _fix_broken_call_args(code)
    fixed, applied_fixes = apply_known_fixes(fixed)
    applied_fixes = structural_fixes + applied_fixes
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
    layout_warnings = run_invariant_warnings(fixed)
    layout_warnings.extend(_check_layout_smells(fixed))
    layout_warnings.extend(_check_loop_timing_smells(fixed))
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
