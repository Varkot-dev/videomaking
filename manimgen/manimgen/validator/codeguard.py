import ast
import os
import re
from collections import Counter
from typing import Any, Callable

from manimgen.validator.invariants import run_all as _run_invariants
from manimgen.validator.manimlib_signatures import (
    _line_col_to_offset,
    _split_source_lines,
    excise_span,
)

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


# The one banned pattern whose match text lives inside a string literal, so it is
# scanned with strings kept (comments are still removed).
_TEX_TEXT_WRAPPER_PATTERN = r"""Tex\(\s*r?['"]\s*\\text\{[^}]*\}\s*['"]\s*[,)]"""

_BANNED_PATTERNS: list[tuple[str, str]] = [
    (
        r"\bfrom\s+manim\s+import\s+\*",
        "Use `from manimlib import *`, not `from manim import *`.",
    ),
    (r"\bMathTex\s*\(", "Use `Tex(...)` in ManimGL, not `MathTex(...)`."),
    (r"\bCreate\s*\(", "Use `ShowCreation(...)` in ManimGL, not `Create(...)`."),
    (r"\bself\.camera\.frame\b", "Use `self.frame`, not `self.camera.frame`."),
    (r"\btip_length\s*=", "Remove `tip_length`; ManimGL Arrow does not support it."),
    (r"\btip_width\s*=", "Remove `tip_width`; ManimGL Arrow does not support it."),
    (r"\btip_shape\s*=", "Remove `tip_shape`; ManimGL Arrow does not support it."),
    (
        r"\bcorner_radius\s*=",
        "Remove `corner_radius`; SurroundingRectangle does not support it.",
    ),
    (
        r"Arrow\(\s*ORIGIN\s*,\s*ORIGIN\s*[,)]",
        "Arrow start/end cannot be the same point.",
    ),
    (
        r"\.get_tex_string\s*\(",
        "Never call .get_tex_string() to read back values — store data in a plain Python list instead (e.g. current_values = [5, 3, 8, 1]) and compare current_values[i] > current_values[j]. Never read values back from Tex/Text mobjects.",
    ),
    (
        r"\.set_fill_color\s*\(",
        "Use .set_fill(color) not .set_fill_color(). ManimGL: obj.set_fill(RED, opacity=1).",
    ),
    (
        r"\.set_text\s*\(",
        "Text has no set_text() method in ManimGL. To update a counter label: create a new Text(...) and use FadeOut(old), FadeIn(new) or ReplacementTransform(old, new).",
    ),
    (
        r"\bscale_factor\s*=",
        "Remove `scale_factor`; FadeIn/FadeOut in ManimGL does not support it.",
    ),
    (
        r"\bCircumscribe\s*\(",
        "Use `FlashAround(...)` in ManimGL, not `Circumscribe(...)`.",
    ),
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
        _TEX_TEXT_WRAPPER_PATTERN,
        r"Remove outer \text{} wrapper from Tex(): use Tex(r'content') not Tex(r'\text{content}'). "
        r"\text{} inside a longer expression like Tex(r'f(x) = \text{label}') is fine.",
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

# VGroup-style names the item-assignment ban applies to. The ban is decided by the
# AST (_vgroup_item_assignment_errors), not by a regex on the spelling alone.
_VGROUP_STYLE_NAMES = frozenset(
    {
        "boxes",
        "labels",
        "cells",
        "group",
        "vgroup",
        "mobs",
        "mobjects",
        "elems",
        "elements",
        "shapes",
        "squares",
        "circles",
        "arrows",
    }
)

_VGROUP_ASSIGN_MESSAGE = (
    "VGroup does not support item assignment. "
    "Use a parallel Python list: box_list = list(boxes), then swap box_list[i], box_list[j]. "
    "Never assign into the VGroup directly."
)


def _blank_spans(code: str, strings: bool) -> str:
    """Return code with comments (and, if `strings`, string literals) blanked out.

    Blanked characters become spaces and newlines are kept, so line numbers do not
    move. Falls back to the raw source when it cannot be tokenized; the SyntaxError
    itself is reported separately by validate_scene_code.
    """
    import io
    import tokenize

    fstring_start = getattr(tokenize, "FSTRING_START", None)
    fstring_end = getattr(tokenize, "FSTRING_END", None)
    offsets = [0]
    for line in code.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)

    def _off(pos: tuple[int, int]) -> int:
        return offsets[pos[0] - 1] + pos[1]

    spans: list[tuple[int, int]] = []
    open_fstring: tuple[int, int] | None = None
    try:
        for tok in tokenize.generate_tokens(io.StringIO(code).readline):
            # Python 3.12+ splits an f-string into FSTRING_START ... FSTRING_END.
            if fstring_start is not None and tok.type == fstring_start:
                open_fstring = tok.start
            elif fstring_end is not None and tok.type == fstring_end:
                if strings and open_fstring is not None:
                    spans.append((_off(open_fstring), _off(tok.end)))
                open_fstring = None
            elif open_fstring is None and (
                tok.type == tokenize.COMMENT
                or (strings and tok.type == tokenize.STRING)
            ):
                spans.append((_off(tok.start), _off(tok.end)))
    except (tokenize.TokenError, SyntaxError):
        return code

    out = list(code)
    for start, end in spans:
        for k in range(start, min(end, len(out))):
            if out[k] != "\n":
                out[k] = " "
    return "".join(out)


def _is_plain_list_value(node: ast.AST | None) -> bool:
    """True for an expression that certainly evaluates to a plain Python list."""
    if isinstance(node, (ast.List, ast.ListComp)):
        return True
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Mult)):
        return _is_plain_list_value(node.left) or _is_plain_list_value(node.right)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
        return node.func.id in ("list", "sorted")
    return False


def _binding_key(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _vgroup_item_assignment_errors(code: str) -> list[str]:
    """Flag item assignment into a VGroup-style name unless it is provably a list.

    `elements = [5, 3, 8, 1]` followed by a swap is plain Python and must pass; the
    old regex banned the spelling of the name whatever it held. A name counts as a
    plain list only when every binding of it in the file is a list literal,
    comprehension or list(...). Parameters, VGroup(...) and anything else keep the
    ban, because the file cannot prove the value supports item assignment.
    """
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return []  # reported by the compile() check

    bound: set[str] = set()
    non_list: set[str] = set()

    def _bind(target: ast.AST, value: ast.AST | None) -> None:
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                _bind(elt, None)
            return
        if isinstance(target, ast.Starred):
            _bind(target.value, None)
            return
        key = _binding_key(target)
        if key is None:
            return
        bound.add(key)
        if not _is_plain_list_value(value):
            non_list.add(key)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                _bind(target, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.NamedExpr)):
            _bind(node.target, getattr(node, "value", None))
        elif isinstance(
            node, (ast.AugAssign, ast.For, ast.AsyncFor, ast.comprehension)
        ):
            _bind(node.target, None)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            _bind(node.optional_vars, None)
        elif isinstance(node, ast.arg):
            bound.add(node.arg)
            non_list.add(node.arg)

    def _item_targets(target: ast.AST):
        if isinstance(target, (ast.Tuple, ast.List)):
            for elt in target.elts:
                yield from _item_targets(elt)
        elif isinstance(target, ast.Starred):
            yield from _item_targets(target.value)
        elif isinstance(target, ast.Subscript):
            yield target

    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            for sub in _item_targets(target):
                root = sub
                while isinstance(root, ast.Subscript):
                    root = root.value
                key = _binding_key(root)
                if key not in _VGROUP_STYLE_NAMES:
                    continue
                if key in bound and key not in non_list:
                    continue
                return [_VGROUP_ASSIGN_MESSAGE]
    return []


_ARROW_CALLEES = frozenset(
    {"Arrow", "Vector", "DoubleArrow", "CurvedArrow", "CurvedDoubleArrow"}
)
_SURFACE_CALLEES = frozenset(
    {
        "Surface",
        "ParametricSurface",
        "Sphere",
        "Torus",
        "Cylinder",
        "Cone",
        "Disk3D",
        "Prism",
        "Cube",
    }
)

# Kwargs ManimGL rejects, mapped to the only callees they are stripped from. A
# kwarg of the same name on any other call is valid (Indicate(scale_factor=),
# RoundedRectangle(corner_radius=)) or a plain name, and is left alone.
_BANNED_KWARGS: dict[str, frozenset[str]] = {
    "tip_length": _ARROW_CALLEES,
    "tip_width": _ARROW_CALLEES,
    "tip_shape": _ARROW_CALLEES,
    # RoundedRectangle takes corner_radius; plain Rectangle-family constructors do not.
    "corner_radius": frozenset({"Rectangle", "Square", "SurroundingRectangle"}),
    # Fade takes scale=; Indicate really does take scale_factor=.
    "scale_factor": frozenset({"FadeIn", "FadeOut"}),
    "target_position": frozenset({"move_to"}),
    # ManimCommunity surface kwarg; ManimGL surfaces have no checkerboard concept.
    # Stripping it (rather than translating) lets the surface render in a solid color.
    "checkerboard_colors": _SURFACE_CALLEES,
}

# ManimCommunity Axes size kwargs, renamed on the calls that take them.
_AXES_CALLEES = frozenset({"Axes", "ThreeDAxes", "NumberPlane", "ComplexPlane"})
_AXES_LENGTH_RENAMES: dict[str, str] = {
    "x_length": "width",
    "y_length": "height",
    "z_length": "depth",
}

# Canonical color-role → ManimGL constant map. Single source of truth, mirrors
# the palette table in generator/prompts/director_system.md. The Director shows
# these roles as a reference but never requires emitting the assignment lines, so
# scenes write `color=MUTED` with MUTED undefined → NameError. When a role is used
# but not defined, _inject_color_role_header() prepends the needed definitions.
_COLOR_ROLE_CONSTANTS: dict[str, str] = {
    "PRIMARY": "TEAL_A",
    "SECONDARY": "GOLD",
    "STRUCT": "GREY_B",
    "INK": "WHITE",
    "MUTED": "GREY_A",
    "SUCCESS": "GREEN",
    "WARNING": "YELLOW",
    "ALERT": "RED",
}


def _callee_name(node: ast.Call) -> str | None:
    """Name a call is made through: ``Foo(...)`` -> Foo, ``x.foo(...)`` -> foo."""
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _edit_call_kwargs(
    code: str,
    plan: Callable[[ast.Call, str | None, ast.keyword], str | None],
) -> tuple[str, list[tuple[str | None, str, str]]]:
    """Strip or rename keyword arguments of specific calls, by AST source span.

    ``plan(call, callee, keyword)`` returns None to leave the keyword alone, ""
    to strip it, or a new name to rename it. Only the keyword's own span is
    touched (plus one bordering comma on a strip), so comments, formatting and
    every other call survive; nothing goes through ``ast.unparse``. A rename that
    would duplicate a keyword already on the call is skipped. Fail-open: code
    that does not parse is returned unchanged. Returns ``(code, edits)`` with one
    ``(callee, old_kwarg, new_kwarg_or_"")`` per edit, in source order.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, []
    lines = _split_source_lines(code)
    # (start, end, new_name, callee, old_name)
    edits: list[tuple[int, int, str, str | None, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        callee = _callee_name(node)
        present = {kw.arg for kw in node.keywords if kw.arg}
        for kw in node.keywords:
            if kw.arg is None:
                continue
            action = plan(node, callee, kw)
            if action is None or (action and action in present):
                continue
            if kw.end_col_offset is None or kw.end_lineno is None:
                continue
            start = _line_col_to_offset(lines, kw.lineno, kw.col_offset)
            end = _line_col_to_offset(lines, kw.end_lineno, kw.end_col_offset)
            edits.append((start, end, action, callee, kw.arg))
    if not edits:
        return code, []
    strips = [(s, e) for s, e, new, _, _ in edits if not new]
    out = code
    done: list[tuple[str | None, str, str]] = []
    for start, end, new, callee, old in sorted(edits, key=lambda e: -e[0]):
        # An edit inside a keyword that is itself being stripped is moot.
        if any(s <= start and end <= e and (s, e) != (start, end) for s, e in strips):
            continue
        if new:
            out = out[:start] + new + out[start + len(old) :]
        else:
            out = excise_span(out, start, end)
        done.append((callee, old, new))
    done.reverse()
    return out, done


# Registry of known-wrong kwarg names per method.
# Maps method_name → {wrong_kwarg: correct_kwarg or None (strip)}.
# Used by both apply_known_fixes (proactive) and apply_error_aware_fixes (reactive).
_KWARG_NORMALIZATION_REGISTRY: dict[str, dict[str, str | None]] = {
    "arrange_in_grid": {
        "rows": "n_rows",
        "cols": "n_cols",
        # ManimGL splits the gap: h_buff between columns, v_buff between rows.
        # Mapping both to buff produced a repeated keyword argument (#55 class).
        "row_buff": "v_buff",
        "col_buff": "h_buff",
    },
    "reorient": {
        "theta_deg": "theta_degrees",
        "phi_deg": "phi_degrees",
    },
    "NumberLine": {
        "label": None,
    },
}


def _apply_registry(
    code: str,
    method: str,
    targets: Callable[[ast.Call], bool] | None = None,
) -> tuple[str, list[tuple[str | None, str, str]]]:
    """Apply every registry fix for ``method`` to the calls made through that name.

    ``targets`` optionally narrows which of those calls are edited (the error-aware
    path uses it to pin the call named by the traceback line).
    """
    norm = _KWARG_NORMALIZATION_REGISTRY[method]

    def plan(call: ast.Call, callee: str | None, kw: ast.keyword) -> str | None:
        if callee != method or kw.arg not in norm:
            return None
        if targets is not None and not targets(call):
            return None
        return norm[kw.arg] or ""

    return _edit_call_kwargs(code, plan)


def _fix_arrange_in_grid_kwargs(code: str) -> tuple[str, str | None]:
    """Normalize all wrong kwarg names on .arrange_in_grid() calls in one pass.

    Correct signature: arrange_in_grid(n_rows=None, n_cols=None, buff=None,
    h_buff=None, v_buff=None, ...).
    LLM commonly emits rows=, cols=, row_buff=, col_buff= simultaneously.
    """
    fixed, edits = _apply_registry(code, "arrange_in_grid")
    if edits:
        applied = [
            f"{old}= → {new or 'stripped'} ({n})"
            for (old, new), n in Counter((o, n) for _, o, n in edits).items()
        ]
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
        # NOTE: fill_color/fill_opacity are NOT rewritten. They are VALID on every
        # VMobject subclass (Square/Circle/Line/Tex/Text/... — vectorized_mobject.py
        # names them explicitly + has **kwargs). The old blanket fill_color->color /
        # fill_opacity->opacity rewrites corrupted that valid code into duplicate
        # color=/opacity= kwargs -> SyntaxError (#55) -> fallback cards. Only the base
        # Mobject / Surface family rejects them; that narrow case is handled by the
        # type-aware introspection shadow (see DESIGN_codeguard_introspection_pivot.md),
        # never by a context-free string rewrite.
        (
            r"\.get_graph_point\s*\(",
            ".input_to_graph_point(",
            "get_graph_point -> input_to_graph_point",
        ),
        # ManimCommunity Circle has get_point_at_angle; ManimGL has point_at_angle.
        (
            r"\.get_point_at_angle\s*\(",
            ".point_at_angle(",
            "get_point_at_angle -> point_at_angle",
        ),
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

    # ManimCommunity Axes uses x_length/y_length; ManimGL uses width/height, and
    # ThreeDAxes z_length -> depth. (x_axis_config/y_axis_config are VALID ManimGL
    # Axes kwargs: do NOT touch.) Renamed only as keywords of the Axes family, never
    # as variable names or kwargs of other calls.
    fixed, renamed = _edit_call_kwargs(
        fixed,
        lambda call, callee, kw: (
            _AXES_LENGTH_RENAMES.get(kw.arg) if callee in _AXES_CALLEES else None
        ),
    )
    for (old, new), count in Counter((o, n) for _, o, n in renamed).items():
        applied.append(f"{old} -> {new} (ManimGL Axes) ({count})")

    for pattern, repl, label in replacements:
        new_fixed, count = re.subn(pattern, repl, fixed)
        if count:
            applied.append(f"{label} ({count})")
            fixed = new_fixed

    # Palette role hex → MaminGL constant (I3). Maps the canonical hex values from
    # COLOR_PALETTE.md (CANONICAL aesthetic) to their ManimGL constant equivalents.
    _HEX_TO_CONSTANT: list[tuple[str, str, str]] = [
        (r'"#00D9FF"', "TEAL_A", "PRIMARY hex -> TEAL_A"),
        (r'"#FF6B35"', "GOLD", "SECONDARY hex -> GOLD"),
        (r'"#3DD17B"', "GREEN", "SUCCESS hex -> GREEN"),
        (r'"#FFC857"', "YELLOW", "WARNING hex -> YELLOW"),
        (r'"#E5484D"', "RED", "ALERT hex -> RED"),
        (r'"#E8E8E8"', "WHITE", "INK hex -> WHITE"),
        (r'"#9A9A9A"', "GREY_A", "MUTED hex -> GREY_A"),
        (r'"#4A4A4A"', "GREY_D", "SUBTLE hex -> GREY_D"),
        (r'"#3A6F8A"', "GREY_B", "STRUCT hex -> GREY_B"),
        (r'"#58C4DD"', "TEAL_B", "legacy TEAL hex -> TEAL_B"),
        (r'"#1C1C1C"', "GREY_E", "dark bg hex -> GREY_E"),
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

    fixed, stripped = _edit_call_kwargs(
        fixed,
        lambda call, callee, kw: (
            "" if callee in _BANNED_KWARGS.get(kw.arg, ()) else None
        ),
    )
    for kw_name, count in Counter(old for _, old, _ in stripped).items():
        applied.append(f"removed {kw_name} ({count})")

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

    fixed, role_applied = _inject_color_role_header(fixed)
    if role_applied:
        applied.append(role_applied)

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

    fixed, ambient_applied = _fix_begin_ambient_camera_rotation(fixed)
    if ambient_applied:
        applied.append(ambient_applied)

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
        # Capture text between "self.play(" and the rect name
        between = code[m.start(0) + len("self.play(") : m.start(1)].strip()
        if between:
            # There's something already there (e.g. ShowCreation()
            result_parts.append(code[pos : m.end(0)])
            pos = m.end(0)
            continue

        # Walk depth-aware from the opening paren of Rectangle(
        rect_open = m.end(0) - 1  # index of '(' after Rectangle name
        depth = 1
        i = rect_open + 1
        while i < len(code) and depth > 0:
            if code[i] == "(":
                depth += 1
            elif code[i] == ")":
                depth -= 1
            i += 1
        rect_close = i - 1  # index of the matching ')'

        # The full rect call including its closing paren
        rect_call = code[m.start(1) : rect_close + 1]

        result_parts.append(code[pos : m.start(1)])
        result_parts.append(f"ShowCreation({rect_call})")
        pos = rect_close + 1
        count += 1

    new_code = "".join(result_parts)
    if count:
        return (
            new_code,
            f"wrapped bare SurroundingRectangle/BackgroundRectangle in ShowCreation ({count})",
        )
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
    """Rewrite self.play(obj.become(...), ...) to self.play(obj.animate.become(...), ...).

    In ManimGL, become() returns self (the mutated Mobject), not an Animation.
    Passing it to self.play() is equivalent to self.play(obj) which crashes with
    "Object X cannot be converted to an animation". `.animate.become(...)` is a
    real animation, so the rewrite is made in place: only the `.animate` is
    inserted, every other argument of the play call (other animations, run_time,
    rate_func) stays exactly as written.

    Only arguments of self.play that are the become call itself (or an element
    of a starred list/generator of them) are rewritten; a become() nested
    deeper, e.g. inside Transform(a, b.become(c)), is a mobject there and stays.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, None
    lines = _split_source_lines(code)

    def is_bare_become(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "become"
            and not (
                isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "animate"
            )
        )

    inserts: list[int] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "play"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
        ):
            continue
        for arg in node.args:
            if isinstance(arg, ast.Starred):
                arg = arg.value
                if isinstance(arg, (ast.ListComp, ast.GeneratorExp)):
                    arg = arg.elt
            if is_bare_become(arg) and arg.func.value.end_lineno is not None:
                inserts.append(
                    _line_col_to_offset(
                        lines, arg.func.value.end_lineno, arg.func.value.end_col_offset
                    )
                )
    if not inserts:
        return code, None
    for at in sorted(set(inserts), reverse=True):
        code = code[:at] + ".animate" + code[at:]
    return (
        code,
        f"self.play(obj.become(...)) -> obj.animate.become(...) ({len(inserts)})",
    )


def _inject_color_role_header(code: str) -> tuple[str, str | None]:
    """Define any color role (PRIMARY/STRUCT/MUTED/...) that is used but undefined.

    Kills the NameError class: the Director writes `color=MUTED` treating the
    palette roles as built-in constants, but never emits the assignment lines.
    For each role referenced as a bare name and not already assigned, we prepend
    `ROLE = CONSTANT`. Injected after the import block so the names are in scope.
    """
    # Detect role USE via AST Name nodes, not raw text (#56): a role word inside a
    # comment ("# use MUTED tones") or a string literal ("Text('SUCCESS')") is NOT a
    # real identifier reference and must not trigger injection. Names in Load context
    # are genuine uses; names in Store context are definitions already present.
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, None  # fail-open: a syntax error is the precheck's job

    used_names: set[str] = set()
    defined_names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                used_names.add(node.id)
            elif isinstance(node.ctx, ast.Store):
                defined_names.add(node.id)

    injected: list[str] = []
    for role, const in _COLOR_ROLE_CONSTANTS.items():
        if role in used_names and role not in defined_names:
            injected.append(f"{role} = {const}")

    if not injected:
        return code, None

    header = (
        "# Color roles (auto-injected by codeguard — see director palette)\n"
        + "\n".join(injected)
        + "\n"
    )

    # Insert after the last TOP-LEVEL import statement (by AST position, so an
    # import inside a method or a continuation line is never mistaken for one) so
    # roles are module-scoped before the Scene class; with no imports, prepend.
    imports = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    if imports:
        lines = _split_source_lines(code)
        insert_at = imports[-1].end_lineno
        new_code = (
            "".join(lines[:insert_at]) + "\n" + header + "".join(lines[insert_at:])
        )
    else:
        new_code = header + "\n" + code

    return new_code, f"injected color-role header ({', '.join(injected)})"


def _replace_self_calls(
    code: str, method: str, build: Callable[[str], str | None]
) -> tuple[str, int]:
    """Replace every ``self.<method>(...)`` call expression by its AST span.

    ``build(args_source)`` returns the replacement text, or None when the call
    cannot be translated; those calls become ``pass`` (when the call is a whole
    statement) or ``None`` (inside a larger expression). Only the call expression
    is replaced, so nested parentheses, multi-line calls and anything that follows
    on the same line (``; next_statement``) are untouched. Fail-open on a syntax
    error. Returns ``(code, replaced_count)``.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return code, 0
    lines = _split_source_lines(code)
    statement_calls = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
    }
    edits: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == method
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "self"
            and node.end_lineno is not None
        ):
            continue
        start = _line_col_to_offset(lines, node.lineno, node.col_offset)
        end = _line_col_to_offset(lines, node.end_lineno, node.end_col_offset)
        src = code[start:end]
        args = src[src.index("(") + 1 : src.rindex(")")]
        new = build(args)
        if new is None:
            new = "pass" if id(node) in statement_calls else "None"
        edits.append((start, end, new))
    # Outermost-last so nested matches (rare) cannot corrupt earlier offsets.
    kept: list[tuple[int, int, str]] = []
    for e in sorted(edits, key=lambda e: (e[0], -e[1])):
        if kept and e[0] < kept[-1][1]:
            continue
        kept.append(e)
    for start, end, new in reversed(kept):
        code = code[:start] + new + code[end:]
    return code, len(kept)


def _fix_set_camera_orientation(code: str) -> tuple[str, str | None]:
    """Rewrite ManimCommunity set_camera_orientation() → self.frame.reorient().

    Handles the most common form:
        self.set_camera_orientation(phi=60 * DEGREES, theta=-45 * DEGREES)
      → self.frame.reorient(-45, 60)

    phi and theta may appear in either order and with optional `* DEGREES` suffix.
    Values without `* DEGREES` are passed through as-is (assumed already in degrees).

    If the call cannot be parsed cleanly, only that call expression is replaced by
    `pass` to prevent AttributeError crashes; the retry LLM receives the
    banned-pattern message.
    """

    def build(args: str) -> str | None:
        phi_m = re.search(r"\bphi\s*=\s*(-?[\d.]+)\s*(?:\*\s*DEGREES)?", args)
        theta_m = re.search(r"\btheta\s*=\s*(-?[\d.]+)\s*(?:\*\s*DEGREES)?", args)
        if phi_m and theta_m:
            return f"self.frame.reorient({theta_m.group(1)}, {phi_m.group(1)})"
        return None

    new, count = _replace_self_calls(code, "set_camera_orientation", build)
    if count:
        return new, f"set_camera_orientation -> self.frame.reorient ({count})"
    return code, None


def _fix_begin_ambient_camera_rotation(code: str) -> tuple[str, str | None]:
    """Rewrite ManimCommunity begin_ambient_camera_rotation() → ManimGL form.

        self.begin_ambient_camera_rotation(rate=0.2)
      → self.frame.add_ambient_rotation(angular_speed=0.2)

    ManimGL has no begin_ambient_camera_rotation (AttributeError on every render);
    the spin lives on the frame as add_ambient_rotation(angular_speed=...). The
    ManimCommunity `rate=` kwarg maps to ManimGL `angular_speed=`. A bare call
    with no args maps to add_ambient_rotation() (its angular_speed defaults).
    Multi-line calls and nested parentheses are handled (#57).
    """

    def build(args: str) -> str:
        rate_m = re.search(r"\brate\s*=\s*(-?[\d.]+)", args)
        if rate_m:
            return f"self.frame.add_ambient_rotation(angular_speed={rate_m.group(1)})"
        # bare call or unrecognized args: use the default spin
        return "self.frame.add_ambient_rotation()"

    new, count = _replace_self_calls(code, "begin_ambient_camera_rotation", build)
    if count:
        return new, f"begin_ambient_camera_rotation -> add_ambient_rotation ({count})"
    return code, None


def _fix_reorient_wrong_kwargs(code: str) -> tuple[str, str | None]:
    """Fix wrong kwarg names on self.frame.reorient() calls.

    The Director sometimes emits:
        self.frame.reorient(theta_deg=-45, phi_deg=60)

    The real param names are theta_degrees= and phi_degrees= (or positional).
    """
    fixed, edits = _apply_registry(code, "reorient")
    if edits:
        applied = [
            f"{old}= -> {new}= ({n})"
            for (old, new), n in Counter((o, n) for _, o, n in edits).items()
        ]
        return fixed, "fixed reorient kwarg names: " + ", ".join(applied)
    return code, None


def _strip_label_kwarg_from_numberline(code: str) -> tuple[str, str | None]:
    """Strip label= kwarg from NumberLine() — not a valid ManimGL parameter."""
    new, edits = _apply_registry(code, "NumberLine")
    if edits:
        return new, f"removed label= from NumberLine ({len(edits)})"
    return code, None


def _fix_broken_call_args(code: str) -> tuple[str, list[str]]:
    """Strip the stray leading comma from calls like get_axis_labels(, y_label=...).

    ``f(, arg)`` is a SyntaxError. Runs only on code that does not compile: valid
    code is never touched (a trailing comma, as in the 1-tuple ``(title,)``, is
    legal Python and changes meaning if removed).
    """
    try:
        ast.parse(code)
    except SyntaxError:
        pass
    else:
        return code, []
    new, count = re.subn(r"\(\s*,\s*", "(", code)
    if count:
        return new, [f"removed leading comma in call args ({count})"]
    return code, []


def _user_traceback_line(stderr: str) -> int | None:
    """Line of the innermost traceback frame that is in the user's scene file."""
    line = None
    for m in re.finditer(r'File "([^"]+)", line (\d+)', stderr):
        path = m.group(1).replace("\\", "/")
        if "manimlib" in path or "site-packages" in path or "dist-packages" in path:
            continue
        line = int(m.group(2))
    return line


def _fix_unexpected_kwarg(
    code: str,
    bad_kw: str,
    method: str | None,
    good_kw: str,
    tb_line: int | None,
    applied: list[str],
) -> str:
    """Repair one "unexpected keyword argument" error on the call that raised it.

    Known-wrong kwargs of ``method`` are fixed from the registry first. Otherwise
    the kwarg is renamed to ``good_kw`` (the traceback's "Did you mean") or, with no
    hint, stripped. Only keywords of the failing call are touched, found as: the
    call made through ``method``; else (the error can come from a base-class
    ``__init__``) a call spanning the traceback line; else the only call in the file
    that carries the kwarg. A same-named kwarg on any other call is never edited.
    """
    if method and method in _KWARG_NORMALIZATION_REGISTRY:
        # Fix ALL known wrong kwargs for this method in one pass, not just the one
        # named in the error. This prevents the one-kwarg-at-a-time peeling pattern.
        new_code, edits = _apply_registry(code, method)
        if edits:
            for (old, new), n in Counter((o, n) for _, o, n in edits).items():
                applied.append(f"registry fix: {old}= → {new or 'stripped'} ({n})")
            return new_code

    def spans_tb_line(call: ast.Call) -> bool:
        end = getattr(call, "end_lineno", call.lineno)
        return tb_line is not None and call.lineno <= tb_line <= end

    carriers = [
        n
        for n in ast.walk(_safe_parse(code))
        if isinstance(n, ast.Call) and any(k.arg == bad_kw for k in n.keywords)
    ]
    scopes: list[Callable[[ast.Call, str | None], bool]] = []
    if method:
        scopes.append(lambda call, callee: callee == method)
    else:
        scopes.append(lambda call, callee: True)
    scopes.append(lambda call, callee: spans_tb_line(call))
    if len(carriers) == 1:
        # Nodes differ between parses, so identify the call by its position.
        at = (carriers[0].lineno, carriers[0].col_offset)
        scopes.append(lambda call, callee: (call.lineno, call.col_offset) == at)

    for scope in scopes:
        new_code, edits = _edit_call_kwargs(
            code,
            lambda call, callee, kw, scope=scope: (
                good_kw if kw.arg == bad_kw and scope(call, callee) else None
            ),
        )
        if edits:
            if good_kw:
                applied.append(f"renamed kwarg '{bad_kw}' → '{good_kw}' ({len(edits)})")
            else:
                applied.append(f"removed unexpected kwarg '{bad_kw}' ({len(edits)})")
            return new_code
    return code


def _safe_parse(code: str) -> ast.AST:
    try:
        return ast.parse(code)
    except SyntaxError:
        return ast.Module(body=[], type_ignores=[])


def apply_error_aware_fixes(code: str, stderr: str) -> tuple[str, list[str]]:
    """Deterministic, token-free repairs driven by actual runtime traceback."""
    fixed = code
    applied: list[str] = []

    # Always run structural syntax fixers regardless of error type
    fixed, structural_fixes = _fix_broken_call_args(fixed)
    applied.extend(structural_fixes)

    if "No such file or directory: 'latex'" in stderr or "latex: not found" in stderr:
        new_fixed, count = re.subn(
            r"\bTex\(\s*str\(([^)]+)\)\s*(,[^)]*)?\)", r"Text(str(\1)\2)", fixed
        )
        if count:
            applied.append(f"Tex(str(...)) -> Text(str(...)) ({count})")
            fixed = new_fixed

        new_fixed, count = re.subn(
            r"\bTex\(\s*f?\"([0-9\.\-]+)\"\s*(,[^)]*)?\)", r'Text("\1"\2)', fixed
        )
        if count:
            applied.append(f"Tex(numeric literal) -> Text(...) ({count})")
            fixed = new_fixed

    if "unexpected keyword argument" in stderr:
        kw_match = re.search(r"got an unexpected keyword argument '(\w+)'", stderr)
        hint_match = re.search(r"Did you mean '(\w+)'\?", stderr)
        # "Arrow.__init__() got ..." names the class, "Mobject.arrange_in_grid()
        # got ..." names the method; take the callable the user wrote, not __init__.
        method_match = re.search(
            r"(?:(\w+)\.)?(\w+)\(\) got an unexpected keyword argument", stderr
        )
        if kw_match:
            bad_kw = kw_match.group(1)
            method = None
            if method_match:
                owner, name = method_match.groups()
                method = owner if name == "__init__" else name
            # font_size= is a valid kwarg on Tex() (handled internally) — do not strip or convert.
            if bad_kw != "font_size":
                fixed = _fix_unexpected_kwarg(
                    fixed,
                    bad_kw,
                    method,
                    hint_match.group(1) if hint_match else "",
                    _user_traceback_line(stderr),
                    applied,
                )

    if "NameError: name '" in stderr:
        name_match = re.search(r"NameError: name '(\w+)' is not defined", stderr)
        if name_match:
            bad_name = name_match.group(1)
            _name_fixes: dict[str, str] = {
                "DARK_GREY": "GREY_D",
                "DARK_GRAY": "GREY_D",
                "DARK_BLUE": "BLUE_D",
                "DARK_GREEN": "GREEN_D",
                "DARK_RED": "RED_D",
                "LIGHT_GREY": "GREY_A",
                "LIGHT_GRAY": "GREY_A",
                "LIGHT_BLUE": "BLUE_A",
                "LIGHT_GREEN": "GREEN_A",
                "LIGHT_RED": "RED_A",
                # Common model mistake: this easing name is not present in ManimGL
                "slow_into_fast": "smooth",
            }
            if bad_name in _name_fixes:
                # Whole identifiers only: TEAL_A must not become TEAL_C_A.
                fixed = re.sub(
                    rf"\b{re.escape(bad_name)}\b", _name_fixes[bad_name], fixed
                )
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
            applied.append(
                f"Transform -> FadeOut/FadeIn (point count mismatch) ({count})"
            )
            fixed = new_fixed

    if "TypeError" in stderr and ".animate" in stderr:
        lines = fixed.split("\n")
        new_lines: list[str] = []
        for line in lines:
            if (
                ".animate" in line
                and ("FadeIn" in line or "FadeOut" in line)
                and "self.play" in line
            ):
                indent = re.match(r"(\s*)", line).group(1)
                parts = re.findall(
                    r"[^,]+\.animate\.[^,]+|FadeIn\([^)]+\)|FadeOut\([^)]+\)", line
                )
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

    # compile(), not ast.parse(): only the compiler rejects errors such as a
    # repeated keyword argument or `return` outside a function.
    try:
        compile(code, "<scene>", "exec", dont_inherit=True)
    except SyntaxError as exc:
        errors.append(f"SyntaxError: {exc.msg} (line {exc.lineno})")

    # Scan code, not prose: a comment or docstring that mentions a banned API
    # ("# avoid boxes[i] = x") must not block a render.
    code_only = _blank_spans(code, strings=True)
    no_comments = _blank_spans(code, strings=False)
    for pattern, message in _BANNED_PATTERNS:
        view = no_comments if pattern == _TEX_TEXT_WRAPPER_PATTERN else code_only
        if re.search(pattern, view):
            errors.append(message)

    errors.extend(_vgroup_item_assignment_errors(code))

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
                    f"(lines {i + 1} and {j + 1}); labels will overlap. "
                    "Use VGroup(...).arrange(DOWN, buff=0.4) and place once."
                )
                break  # one warning per anchor is enough


_TOP_EDGE_PLACEMENT_RE = re.compile(
    r"(?:"
    r"\b(\w+)\s*=\s*[^#\n]*?\.to_edge\s*\(\s*UP\b"
    r"|\b(\w+)\s*=\s*[^#\n]*?\.to_corner\s*\(\s*U[LR]\b"
    r"|\b(\w+)\.animate\.to_edge\s*\(\s*UP\b"
    r")",
)
_FADEOUT_NAMES_RE = re.compile(r"FadeOut\s*\(\s*(\w+)")


def _check_top_edge_collision(lines: list[str], warnings: list[str]) -> None:
    """Warn when 2+ Text/Tex mobjects occupy the title zone without intervening FadeOut.

    The title zone (y > 2.5) holds at most ONE mobject at a time. When a second
    .to_edge(UP) / .to_corner(UR|UL) / .animate.to_edge(UP) call appears, the
    code must have FadeOut(prev_var) between the two — otherwise both end up
    stacked at the top edge and the text overlaps illegibly.

    This was the root cause of the dot-product video's Section 4 (3 titles at
    UP same y) and Section 6 (residual section title under conclusion) layout
    bugs. The Director keeps making the same mistake under generation pressure.
    """
    pending_top: list[tuple[int, str]] = []  # (line_idx, var_name)

    for i, line in enumerate(lines):
        # FadeOut() clears matching vars from the pending list.
        for m in _FADEOUT_NAMES_RE.finditer(line):
            name = m.group(1)
            pending_top = [(idx, n) for (idx, n) in pending_top if n != name]

        m = _TOP_EDGE_PLACEMENT_RE.search(line)
        if not m:
            continue
        var = m.group(1) or m.group(2) or m.group(3)
        if not var:
            continue
        if pending_top:
            other_names = ", ".join(n for _, n in pending_top)
            warnings.append(
                f"Line {i + 1}: '{var}' placed in title zone (UP edge / UR-UL corner) "
                f"while prior top-edge mobject(s) ({other_names}) have not been faded out. "
                f"The title zone holds ONE mobject at a time — add FadeOut({other_names}) "
                f"before introducing '{var}', or both will visibly overlap."
            )
        pending_top.append((i, var))


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

        body_text = "\n".join(lines[i + 1 : loop_end])

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
                    re.search(rf"\b{re.escape(v)}\b", la) for v in accumulated_vars
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


# Rough per-glyph aspect: an average glyph occupies ~0.55 * (font_size/72) manim
# units of horizontal space (manim renders ~1 unit ≈ 72px at default scale). This
# is deliberately approximate — it only needs to separate a clearly-too-wide title
# from a normal one, not measure exact pixels. Tuned so the real ~49-char title at
# fs=36 (~13.5 units) trips the ~13-unit threshold while a normal ~22-char title at
# fs=48 (~8 units) stays well under.
_GLYPH_ASPECT = 0.55
# Usable horizontal width: the 14.2-unit frame minus side margins/buffs.
_USABLE_FRAME_WIDTH = 13.0

# Title at the UP edge: `var = Text(/Tex("...", font_size=NN, ...)....to_edge(UP`
# Captures the title string content, the font_size, on a line that also places it
# at the top edge. font_size may appear before or after the string within the call.
_TITLE_TEXT_RE = re.compile(
    r"""(?:Text|Tex|TexText)\s*\(\s*r?["'](?P<content>[^"']*)["']""",
)
_FONT_SIZE_RE = re.compile(r"\bfont_size\s*=\s*(\d+)")
_TOP_EDGE_ON_LINE_RE = re.compile(r"\.to_edge\s*\(\s*UP\b|\.to_corner\s*\(\s*U[LR]\b")


def _check_title_width_overflow(lines: list[str], warnings: list[str]) -> None:
    """Warn when a Text/Tex title at the UP edge is likely too wide to fit.

    Real defect: Text("The Algorithm: Pointers, Midpoint, and Comparison",
    font_size=36).to_edge(UP) — 53 chars at fs=36 estimates to ~13.25 units,
    overflowing the ~13-unit usable frame and rendering clipped/garbled.

    Estimate: char_count * _GLYPH_ASPECT * (font_size / 72) manim units.
    A normal ~24-char title at fs=36 (~6 units) or ~22-char title at fs=48
    (~7.3 units) stays well under the threshold and is NOT flagged.
    """
    default_font_size = 48  # manim Text() default; conservative upper bound
    for i, line in enumerate(lines):
        if not _TOP_EDGE_ON_LINE_RE.search(line):
            continue
        m = _TITLE_TEXT_RE.search(line)
        if not m:
            continue
        content = m.group("content")
        char_count = len(content)
        fs_m = _FONT_SIZE_RE.search(line)
        font_size = int(fs_m.group(1)) if fs_m else default_font_size
        est_width = char_count * _GLYPH_ASPECT * (font_size / 72)
        if est_width > _USABLE_FRAME_WIDTH:
            warnings.append(
                f"Line {i + 1}: title is likely too wide — '{content[:40]}...' "
                f"({char_count} chars at font_size={font_size}) estimates to "
                f"~{est_width:.1f} manim units, exceeding the ~{_USABLE_FRAME_WIDTH:.0f}-unit "
                "usable frame width and will render clipped/garbled. "
                "Shorten the title text or reduce font_size."
            )


# A large horizontal array: VGroup built from a list comprehension that iterates
# over a source list, where that source list literal has 8+ elements. Captures the
# source list variable name so we can size it.
_ARRAY_COMP_RE = re.compile(
    r"VGroup\s*\(\s*\*\s*\[[^\]]*?\bfor\b\s+\w+\s+in\s+(\w+)\s*\]"
)
_LIST_LITERAL_RE = re.compile(r"^\s*(\w+)\s*=\s*\[([^\]]*)\]")
# Opposing horizontal shift on the same line: .shift(LEFT * n) / .shift(RIGHT * n).
# Also detect a vertical component so we can tell same-band from stacked rows.
_SHIFT_LEFT_RE = re.compile(r"\.shift\([^)]*\bLEFT\b")
_SHIFT_RIGHT_RE = re.compile(r"\.shift\([^)]*\bRIGHT\b")
_SHIFT_VERTICAL_RE = re.compile(r"\.shift\([^)]*\b(?:UP|DOWN)\b")

# Conservative threshold: only flag arrays large enough that two of them with
# opposing shifts plausibly collide in the middle.
_LARGE_ARRAY_MIN_ELEMENTS = 8


def _check_side_by_side_array_overflow(lines: list[str], warnings: list[str]) -> None:
    """Warn when two large horizontal arrays with opposing shifts share a band.

    Real defect: two 10-element VGroups of Squares, one .shift(LEFT*2.8) and one
    .shift(RIGHT*2.8) on the same vertical band, collide in the middle.

    Conservative: requires BOTH groups to be built from a list comprehension over
    an 8+ element source list, AND placed with opposing horizontal shifts (one
    LEFT, one RIGHT) on the same vertical band (neither shifted vertically apart).
    A single array, two small groups, same-direction shifts, or vertically
    separated rows are NOT flagged.
    """
    # Map source-list variable -> element count, for list literals with enough items.
    list_sizes: dict[str, int] = {}
    for line in lines:
        lm = _LIST_LITERAL_RE.match(line)
        if not lm:
            continue
        items = [tok for tok in lm.group(2).split(",") if tok.strip()]
        list_sizes[lm.group(1)] = len(items)

    # Collect large-array rows: (line_idx, horizontal_dir, has_vertical_shift)
    left_rows: list[bool] = []  # has_vertical_shift flags for LEFT-shifted large arrays
    right_rows: list[bool] = []

    for line in lines:
        cm = _ARRAY_COMP_RE.search(line)
        if not cm:
            continue
        source = cm.group(1)
        if list_sizes.get(source, 0) < _LARGE_ARRAY_MIN_ELEMENTS:
            continue
        has_vertical = bool(_SHIFT_VERTICAL_RE.search(line))
        if _SHIFT_LEFT_RE.search(line):
            left_rows.append(has_vertical)
        elif _SHIFT_RIGHT_RE.search(line):
            right_rows.append(has_vertical)

    # Need at least one LEFT-shifted and one RIGHT-shifted large array, both on the
    # same band (no vertical separation), for a middle collision.
    same_band_left = any(not v for v in left_rows)
    same_band_right = any(not v for v in right_rows)
    if same_band_left and same_band_right:
        warnings.append(
            "Two large side-by-side arrays detected: each VGroup is built from an "
            f"{_LARGE_ARRAY_MIN_ELEMENTS}+ element list and they are placed with "
            "opposing horizontal shifts (LEFT*n and RIGHT*n) on the same vertical "
            "band — they will collide/overlap in the middle. Stack them vertically "
            "(one .shift(UP...), one .shift(DOWN...)), shrink each row "
            "(smaller side_length / fewer visible elements), or show one array at a time."
        )


def _check_layout_smells(code: str) -> list[str]:
    """Codeguard-local layout heuristics that are not design-system invariants.

    Design-system invariants (I2/I3/I4/I5/I7/I9) live in invariants.py and are
    surfaced via run_invariant_warnings. This function covers mechanical ManimGL
    traps: axes sizing, axes tick font, stacking on shared anchors, horizontal
    overflow chains. These are local to codeguard because they relate to
    specific ManimGL API pitfalls, not to abstract design rules.
    """
    warnings: list[str] = []
    if re.search(r"\bAxes\s*\(", code) and not re.search(
        r"\.set_width\s*\(|x_length\s*=|width\s*=|height\s*=", code
    ):
        warnings.append(
            "Axes created without .set_width(); axes will render at default internal size, "
            "producing dead space or overflow. Use .set_width(10).center() "
            "(add .shift(DOWN * 0.5) if a title is present)."
        )
    if re.search(r"axes\.move_to\s*\(\s*ORIGIN\s*\)", code) and not re.search(
        r"\.set_width\s*\(", code
    ):
        warnings.append(
            "axes.move_to(ORIGIN) used without .set_width(); this does not resize axes. "
            "Replace with axes.set_width(10).center() (or .center().shift(DOWN * 0.5) with a title)."
        )
    if re.search(r"\.move_to\s*\(\s*axes\.(?:c2p|i2gp|get_center)", code):
        warnings.append("Label moved into axes area; this often overlaps curves/ticks.")

    lines = code.strip().splitlines()
    _check_next_to_stacking(lines, warnings)
    _check_horizontal_chain_overflow(lines, warnings)
    _check_top_edge_collision(lines, warnings)
    _check_title_width_overflow(lines, warnings)
    _check_side_by_side_array_overflow(lines, warnings)

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
                'decimal_number_config={"font_size": 24} inside axis_config.'
            )
        if re.search(r"\baxis_config\s*=\s*\{[^{}]*[\"']font_size[\"']", code):
            warnings.append(
                "font_size passed directly in axis_config will crash (TypeError). "
                "Nest it inside decimal_number_config: "
                'axis_config={"decimal_number_config": {"font_size": 24}}.'
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
        return new, f"forced y_axis include_numbers=False ({count})"
    return code, None


def _shadow_log_unknown_symbols(code: str) -> list[str]:
    """Report-only (#30): log manimlib symbols the allowlist *would* flag.

    Pure shadow mode this cycle — it NEVER blocks the render, never adds to
    ``errors`` or ``layout_warnings``, and never degrades output. The goal is to
    land the allowlist mechanism + shadow logging so enforcement can be gated on
    real data later. Fail-open: when ``manimlib`` is unavailable (CI) the check
    is a no-op. Returns the flagged names (for tests); callers ignore the value.
    """
    from manimgen.validator.manimlib_symbols import shadow_check_allowlist

    flagged = shadow_check_allowlist(code)
    if flagged:
        import logging

        logging.getLogger(__name__).info(
            "[codeguard][allowlist-shadow] would flag %d unknown symbol(s) "
            "(report-only, render NOT blocked): %s",
            len(flagged),
            ", ".join(flagged),
        )
        # Persist so real runs accumulate evidence (see evidence_log docstring).
        from manimgen.validator.evidence_log import log_event

        log_event("shadow_unknown_symbols", count=len(flagged), symbols=sorted(flagged))
    return flagged


def _shadow_log_invalid_kwargs(code: str) -> list:
    """Report-only (Phase 2): log constructor kwargs the introspection check *would*
    flag as provably invalid for the resolved manimlib class.

    Pure shadow mode — NEVER blocks the render, never adds to ``errors`` or
    ``layout_warnings``, never alters code. Lands the type-aware mechanism + shadow
    logging so enforcement can be gated on real data later (see
    docs/DESIGN_codeguard_introspection_pivot.md). Fail-open: when ``manimlib`` is
    unavailable (CI) it is a no-op. Returns the flagged items (for tests).
    """
    from manimgen.validator.manimlib_signatures import shadow_check_kwargs

    flagged = shadow_check_kwargs(code)
    if flagged:
        import logging

        logging.getLogger(__name__).info(
            "[codeguard][kwarg-shadow] would flag %d invalid kwarg(s) "
            "(report-only, render NOT blocked): %s",
            len(flagged),
            ", ".join(
                f"{fk.class_name}(...{fk.kwarg}=) L{fk.lineno}" for fk in flagged
            ),
        )
        from manimgen.validator.evidence_log import log_event

        log_event(
            "shadow_invalid_kwargs",
            count=len(flagged),
            kwargs=[
                {"class": fk.class_name, "kwarg": fk.kwarg, "lineno": fk.lineno}
                for fk in flagged
            ],
        )
    return flagged


def precheck_and_autofix(code: str) -> str:
    """Apply all known auto-fixes to a code string and return the fixed code.

    Called by scene_generator before saving the file. Also called by retry.py
    on the file path (see precheck_and_autofix_file for that variant).
    """
    fixed, _ = _precheck_and_autofix_verbose(code)
    return fixed


def _precheck_and_autofix_verbose(code: str) -> tuple[str, list[str]]:
    """Same repair path as precheck_and_autofix, but also returns the rule labels.

    Split out so the evidence log can record WHICH rules fired without changing
    precheck_and_autofix's public `str` return type, which callers rely on.
    """
    # Fix structural syntax errors first (leading/trailing commas in calls)
    fixed, structural_fixes = _fix_broken_call_args(code)
    fixed, applied_fixes = apply_known_fixes(fixed)
    applied_fixes = structural_fixes + applied_fixes

    # Phase 3 enforcement (default OFF — gated on MANIMGEN_KWARG_ENFORCE). When
    # enabled, surgically REMOVE provably-invalid constructor kwargs via the
    # type-aware introspection strip. Removal can't create a duplicate kwarg, so
    # this can't reintroduce #55. Dormant until shadow data justifies turning on.
    from manimgen.validator.manimlib_signatures import (
        enforcement_enabled,
        strip_invalid_kwargs,
    )

    if enforcement_enabled():
        fixed, removed = strip_invalid_kwargs(fixed)
        if removed:
            applied_fixes = applied_fixes + [
                f"stripped invalid kwarg {cls}(...{kw}=)" for cls, kw in removed
            ]

    if applied_fixes:
        import logging

        logging.getLogger(__name__).debug("[codeguard] applied: %s", applied_fixes)
    return fixed, applied_fixes


def precheck_and_autofix_file(scene_path: str) -> dict[str, Any]:
    """Read a scene file, apply auto-fixes, write back, return result dict."""
    with open(scene_path, encoding="utf-8") as f:
        code = f.read()

    fixed, applied_fixes = _precheck_and_autofix_verbose(code)
    if fixed != code:
        with open(scene_path, "w", encoding="utf-8") as f:
            f.write(fixed)

    _shadow_log_unknown_symbols(fixed)
    _shadow_log_invalid_kwargs(fixed)

    errors = validate_scene_code(fixed)
    layout_warnings = run_invariant_warnings(fixed)
    layout_warnings.extend(_check_layout_smells(fixed))
    layout_warnings.extend(_check_loop_timing_smells(fixed))

    # The resolution-rate signal: did the static repair path leave this scene in a
    # state that passes Codeguard's own validation? Aggregated by
    # eval/aggregate_logs.py into the same metric eval/run_corpus.py reports, so a
    # real production run can confirm or refute the corpus number.
    from manimgen.validator.evidence_log import log_event

    log_event(
        "precheck",
        scene=os.path.basename(scene_path),
        code_changed=fixed != code,
        rules_fired=applied_fixes,
        rules_fired_count=len(applied_fixes),
        validation_clean=not errors,
        error_count=len(errors),
        first_error=errors[0] if errors else None,
        layout_warning_count=len(layout_warnings),
    )

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
