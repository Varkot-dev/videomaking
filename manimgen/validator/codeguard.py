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
    (r"\.get_tex_string\s*\(", "Never call .get_tex_string() to read back values — store data in a plain Python list instead (e.g. current_values = [5, 3, 8, 1]) and compare current_values[i] > current_values[j]. Never read values back from Tex/Text mobjects."),
    (r"\.set_fill_color\s*\(", "Use .set_fill(color) not .set_fill_color(). ManimGL: obj.set_fill(RED, opacity=1)."),
    (r"\.set_text\s*\(", "Text has no set_text() method in ManimGL. To update a counter label: create a new Text(...) and use FadeOut(old), FadeIn(new) or ReplacementTransform(old, new)."),
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
        # ManimCommunity Axes uses x_length/y_length; ManimGL uses width/height
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

    new_fixed, count = re.subn(
        r"self\.wait\(\s*(?:-\s*[\d.]+|0+\.0+|(?<!\d)0(?![\d.]))\s*\)",
        "self.wait(0.01)",
        fixed,
    )
    if count:
        applied.append(f"clamped negative/zero self.wait() to 0.01 ({count})")
        fixed = new_fixed

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
            # font_size= is a valid kwarg on Tex() (handled internally) — do not strip or convert.
            # Only strip genuinely unexpected kwargs.
            if bad_kw != "font_size":
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

    errors.extend(_detect_tmt_on_text(code))

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
