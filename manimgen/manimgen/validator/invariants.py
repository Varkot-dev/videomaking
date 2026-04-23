"""Design-system invariants as a single data-driven registry.

Each invariant is a row: (id, severity, check_fn). Adding a new rule is
one entry in the registry — callers iterate, not branch. Severity is
an enum: ERROR blocks the render (wired via codeguard.validate_scene_code
→ retry.py precheck path), WARNING surfaces guidance to the retry LLM.

The check functions are pure str → list[str]: testable without pipeline
state, swappable without editing call sites.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Callable


class Severity(enum.Enum):
    ERROR = "error"      # blocks render; retry LLM sees it via precheck stderr
    WARNING = "warning"  # logged to pipeline output; surfaced to retry LLM


@dataclass(frozen=True)
class Invariant:
    id: str                              # stable ID, e.g. "I1_mobject_explosion"
    severity: Severity
    check: Callable[[str], list[str]]    # returns [] if ok, else list of messages


# ── Individual invariant checks ──────────────────────────────────────────────
# Each is a pure function. No pipeline state. No imports from the rest of the
# validator — these live at the bottom of the dependency tree.

# Canonical design-system constants. Centralized here so changing the scale
# is a one-line edit, not a scattered grep.
_CANONICAL_FONT_SIZES = frozenset({48, 44, 36, 28, 22, 20, 18})
_SANCTIONED_HEXES = frozenset({
    "#1C1C1C",   # render background (manimgl -c flag)
    "#2a2a2a",   # STRUCT fill — design-system-sanctioned, see director_system.md
})
# Role constants: raw usage flagged so the Director uses role-semantic wrappers
# (colors as roles, not raw constants). RED/GREEN/YELLOW have strong semantic
# meaning in a design system; we want the LLM to name the role it's invoking.
_ROLE_CONSTANTS = frozenset({"RED", "GREEN", "YELLOW"})


def _I1_mobject_explosion(code: str) -> list[str]:
    """Reject VGroup(*[<expr> for _ in range(N)]) where N > 50."""
    errors: list[str] = []
    pattern = re.compile(
        r"VGroup\s*\(\s*\*\s*\[.*?for\s+\w+\s+in\s+range\s*\(\s*(\d+)\s*\).*?\]\s*\)",
        re.DOTALL,
    )
    for m in pattern.finditer(code):
        n = int(m.group(1))
        if n > 50:
            errors.append(
                f"I1: VGroup with range({n}) — rendering {n} mobjects per frame is infeasible. "
                f"Use a representative grid of ≤16 elements and a label (e.g. '× {n}') "
                f"to communicate scale symbolically."
            )
    return errors


def _I3_raw_hex(code: str) -> list[str]:
    """Flag raw hex literals outside the sanctioned set.

    Warning, not error: the auto-fix in apply_known_fixes._HEX_TO_CONSTANT
    handles the common cases. This catches the residue (non-palette hex).
    """
    warnings: list[str] = []
    hex_re = re.compile(r'"(#[0-9A-Fa-f]{3,6})"')
    seen: set[str] = set()
    sanctioned = {h.upper() for h in _SANCTIONED_HEXES}
    for m in hex_re.finditer(code):
        hx = m.group(1).upper()
        if hx in sanctioned or hx in seen:
            continue
        seen.add(hx)
        warnings.append(
            f"I3: raw hex color {m.group(1)!r} — use a ManimGL constant "
            f"(PRIMARY=TEAL_A, SECONDARY=GOLD, STRUCT=GREY_B, SUCCESS=GREEN, "
            f"WARNING=YELLOW, ALERT=RED, INK=WHITE)."
        )
    return warnings


def _I3_role_constants(code: str) -> list[str]:
    """Warn when RED/GREEN/YELLOW are used semantically (fill/stroke/color=).

    These constants exist in ManimGL but carry role meaning in the design
    system: RED=ALERT, GREEN=SUCCESS, YELLOW=WARNING. Using them outside
    those roles dilutes the visual vocabulary.
    """
    warnings: list[str] = []
    for role in _ROLE_CONSTANTS:
        pattern = re.compile(
            rf"(?:color\s*=\s*|set_fill\s*\(\s*|set_stroke\s*\(\s*|fill_color\s*=\s*|stroke_color\s*=\s*){role}\b"
        )
        if pattern.search(code):
            role_label = {
                "RED": "ALERT (wrong/invalid)",
                "GREEN": "SUCCESS (correct/solved)",
                "YELLOW": "WARNING (caution/highlight)",
            }[role]
            warnings.append(
                f"I3: raw {role} used semantically — confirm the role is {role_label}. "
                f"If the color is decorative only, prefer a palette role (TEAL_A=PRIMARY, "
                f"GOLD=SECONDARY). Max 3 hues per cue."
            )
    return warnings


def _I4_font_size_scale(code: str) -> list[str]:
    """Warn when font_size is not in the canonical scale.

    The auto-fix _fix_font_size_to_scale snaps off-scale sizes to the nearest
    canonical value before render. This warning catches values that slipped
    past (e.g. inside a computed expression, though rare).
    """
    warnings: list[str] = []
    for m in re.finditer(r"\bfont_size\s*=\s*(\d+)", code):
        fs = int(m.group(1))
        if fs not in _CANONICAL_FONT_SIZES:
            warnings.append(
                f"I4: font_size={fs} is not canonical — use one of "
                f"{sorted(_CANONICAL_FONT_SIZES, reverse=True)} "
                f"(48 TITLE, 44 EQUATION, 36 SUBTITLE, 28 BODY, 22 LABEL, 20 TICK, 18 CAPTION)."
            )
    return warnings


def _I5_missing_final_fadeout(code: str) -> list[str]:
    """Every scene must end with a full FadeOut of self.mobjects."""
    lines = code.strip().splitlines()
    if not lines:
        return []
    tail = "\n".join(lines[-15:])
    if "FadeOut" not in "\n".join(lines) or (
        "self.mobjects" not in tail or "FadeOut" not in tail
    ):
        return [
            "I5: scene does not end with a full FadeOut — add "
            "`self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)` "
            "as the final line of the last cue."
        ]
    return []


def _I7_threed_fix_in_frame(code: str) -> list[str]:
    """In ThreeDScene, Text/Tex must call .fix_in_frame() within 5 lines."""
    warnings: list[str] = []
    if not re.search(r"class\s+\w+\s*\(\s*ThreeDScene\s*\)", code):
        return warnings
    lines = code.splitlines()
    assign_re = re.compile(r"^\s*(\w+)\s*=\s*(?:Text|Tex)\s*\(")
    for i, line in enumerate(lines):
        m = assign_re.match(line)
        if not m:
            continue
        lookahead = "\n".join(lines[i + 1: i + 6])
        if ".fix_in_frame()" not in lookahead:
            warnings.append(
                f"I7: ThreeDScene: '{m.group(1)}' (line {i + 1}) has no "
                f".fix_in_frame() within 5 lines — text will tilt with the 3D camera."
            )
    return warnings


def _I9_mobject_density(code: str) -> list[str]:
    """Warn when > 10 distinct mobjects are created (>7 visible per cue is the rule)."""
    creation_re = re.compile(
        r"^\s*(\w+)\s*=\s*(?:Text|Tex|Dot|Circle|Arrow|Line|Rectangle|VGroup|Axes|NumberLine|ParametricSurface)\s*\(",
        re.MULTILINE,
    )
    created = {m.group(1) for m in creation_re.finditer(code)}
    if len(created) > 10:
        return [
            f"I9: high mobject density — {len(created)} distinct objects created. "
            f"Group related objects with VGroup; verify ≤7 visible at HOLD phase."
        ]
    return []


def _I2_zone_grammar(code: str) -> list[str]:
    """to_edge(UP) reserved for the section title; content belongs in CONTENT zone."""
    warnings: list[str] = []
    up_edge_count = len(re.findall(r"\.to_edge\s*\(\s*UP\b", code))
    if up_edge_count > 1:
        warnings.append(
            "I2: Multiple .to_edge(UP) calls detected — only the section title should "
            "use to_edge(UP). Use explicit .move_to() for secondary top-region elements."
        )
    if re.search(r"\b(axes|curve|graph)\s*\.to_edge\s*\(\s*UP\b", code):
        warnings.append(
            "I2: content object (axes/curve/graph) placed with .to_edge(UP) — this invades "
            "TITLE zone y∈[2.6,4.0]. Use .center().shift(DOWN * 0.8) instead."
        )
    return warnings


def _I2_corner_title(code: str) -> list[str]:
    """Titles must never be placed with .to_corner().

    Matches lines that both introduce a title-named variable and call .to_corner().
    Covers both `title = foo.to_corner(UL)` and `title.to_corner(UR)` forms.
    """
    warnings: list[str] = []
    for line in code.splitlines():
        if ".to_corner" not in line:
            continue
        if re.search(r"\b\w*title\w*\b", line, re.IGNORECASE):
            warnings.append(
                "I2: title placed with .to_corner() — titles must use .to_edge(UP, buff=0.8). "
                "For split-screen, use .to_edge(UP, buff=0.8).shift(LEFT*3.2) / .shift(RIGHT*3.2)."
            )
            break
    return warnings


# ── The registry ─────────────────────────────────────────────────────────────
# Adding an invariant is one row here. Call sites iterate; they do not branch
# on id or severity. The order is stable: ERRORs first, then WARNINGs, so
# callers that only want errors stop reading early.

INVARIANTS: tuple[Invariant, ...] = (
    # Errors — block the render
    Invariant("I1_mobject_explosion", Severity.ERROR, _I1_mobject_explosion),

    # Warnings — surface guidance without blocking
    Invariant("I2_zone_grammar",       Severity.WARNING, _I2_zone_grammar),
    Invariant("I2_corner_title",       Severity.WARNING, _I2_corner_title),
    Invariant("I3_raw_hex",            Severity.WARNING, _I3_raw_hex),
    Invariant("I3_role_constants",     Severity.WARNING, _I3_role_constants),
    Invariant("I4_font_size_scale",    Severity.WARNING, _I4_font_size_scale),
    Invariant("I5_missing_final_fadeout", Severity.WARNING, _I5_missing_final_fadeout),
    Invariant("I7_threed_fix_in_frame", Severity.WARNING, _I7_threed_fix_in_frame),
    Invariant("I9_mobject_density",    Severity.WARNING, _I9_mobject_density),
)


def run_all(code: str) -> tuple[list[str], list[str]]:
    """Run every invariant. Return (errors, warnings) with IDs prefixed.

    errors block the render via codeguard.validate_scene_code. warnings
    are surfaced to the retry LLM via the layout_warnings channel.
    """
    errors: list[str] = []
    warnings: list[str] = []
    for inv in INVARIANTS:
        messages = inv.check(code)
        target = errors if inv.severity is Severity.ERROR else warnings
        target.extend(messages)
    return errors, warnings
