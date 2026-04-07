"""
TemplateScene base class.
All templates inherit from this. Handles code generation, file writing,
title rendering pattern, and clean exit pattern.
"""
import re
import textwrap
from pathlib import Path

# ManimGL color constants — used to normalise lowercase/mixed-case color strings.
_MANIMGL_COLORS = {
    "WHITE", "BLACK", "GREY", "GRAY",
    "GREY_A", "GREY_B", "GREY_C", "GREY_D", "GREY_E",
    "GRAY_A", "GRAY_B", "GRAY_C", "GRAY_D", "GRAY_E",
    "BLUE", "BLUE_A", "BLUE_B", "BLUE_C", "BLUE_D", "BLUE_E",
    "TEAL", "TEAL_A", "TEAL_B", "TEAL_C", "TEAL_D", "TEAL_E",
    "GREEN", "GREEN_A", "GREEN_B", "GREEN_C", "GREEN_D", "GREEN_E",
    "YELLOW", "YELLOW_A", "YELLOW_B", "YELLOW_C", "YELLOW_D", "YELLOW_E",
    "GOLD", "GOLD_A", "GOLD_B", "GOLD_C", "GOLD_D", "GOLD_E",
    "RED", "RED_A", "RED_B", "RED_C", "RED_D", "RED_E",
    "MAROON", "MAROON_A", "MAROON_B", "MAROON_C", "MAROON_D", "MAROON_E",
    "PURPLE", "PURPLE_A", "PURPLE_B", "PURPLE_C", "PURPLE_D", "PURPLE_E",
    "PINK", "LIGHT_PINK",
    "ORANGE", "LIGHT_BROWN", "DARK_BROWN",
    "BLUE_D", "GREEN_D", "RED_D",
    "TEAL_C", "MAROON_B", "PURPLE_B",
    "ORIGIN",
}


def normalize_color(color: str) -> str:
    """Return a safe ManimGL color expression from a spec color value.

    - Hex strings like '#FF0000' → '"#FF0000"' (quoted, so generated code is valid)
    - Known ManimGL constants (case-insensitive) → uppercased, e.g. 'yellow' → 'YELLOW'
    - Already-uppercase known constants → pass through unchanged
    - Anything unrecognised → pass through unchanged (codeguard will catch it)
    """
    if not isinstance(color, str):
        return str(color)
    stripped = color.strip()
    if re.match(r'^#[0-9A-Fa-f]{6}$', stripped):
        return f'"{stripped}"'
    upper = stripped.upper()
    if upper in _MANIMGL_COLORS:
        return upper
    # Already looks like a proper constant (all caps + underscores + digits)
    if re.match(r'^[A-Z][A-Z0-9_]*$', stripped):
        return stripped
    # Unknown — uppercase it as a best-effort and let codeguard handle it
    return upper


class TemplateScene:
    """
    Base for all template code generators. Subclasses implement:
      - render_beat(beat: dict) -> str   (returns lines of Python code)
      - scene_setup() -> str            (optional: declarations before beat loop)

    Each template is a code-generator, not a live Scene. from_spec() writes
    a .py file containing exactly one ManimGL Scene class.
    """

    SCENE_BASE_CLASS = "Scene"

    @classmethod
    def from_spec(cls, spec: dict, class_name: str, output_path: str) -> str:
        generator = cls(spec)
        code = generator._generate(class_name)
        Path(output_path).write_text(code)
        return code

    def __init__(self, spec: dict):
        self.spec = spec
        self.title = spec.get("title", "")
        self.beats = spec.get("beats", [])
        self.duration = spec.get("duration_seconds", 10.0)
        # Set by render_beat() implementations when they emit a full FadeOut.
        # Suppresses the duplicate cleanup at the end of construct().
        self._last_beat_cleared_scene: bool = False

    def _generate(self, class_name: str) -> str:
        lines = ["from manimlib import *", "", ""]
        lines.append(f"class {class_name}({self.SCENE_BASE_CLASS}):")
        lines.append("    def construct(self):")

        body_lines = self._build_construct_body()
        for line in body_lines:
            if line == "":
                lines.append("")
            else:
                lines.append("        " + line)

        return "\n".join(lines) + "\n"

    def _build_construct_body(self) -> list[str]:
        lines: list[str] = []

        if self.title:
            lines += self._render_title()
            lines.append("")

        lines += self.scene_setup()

        self._last_beat_cleared_scene = False
        for beat in self.beats:
            beat_lines = self.render_beat(beat)
            if beat_lines:
                lines += beat_lines
                lines.append("")

        # Only emit cleanup if the last beat didn't already FadeOut everything.
        if not self._last_beat_cleared_scene:
            lines += self._render_cleanup()
        return lines

    def scene_setup(self) -> list[str]:
        return []

    def render_beat(self, beat: dict) -> list[str]:
        return []

    def _render_title(self) -> list[str]:
        escaped = self.title.replace('"', '\\"')
        return [
            f'title = Text("{escaped}", font_size=48, color=BLUE)',
            'title.to_edge(UP, buff=0.8)',
            'self.play(Write(title), run_time=1.0)',
            'self.wait(0.3)',
        ]

    def _render_cleanup(self) -> list[str]:
        return [
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)",
            "self.wait(0.5)",
        ]

    @staticmethod
    def indent(lines: list[str], spaces: int = 4) -> list[str]:
        pad = " " * spaces
        return [pad + line if line else "" for line in lines]

    @staticmethod
    def dedent(code: str) -> str:
        return textwrap.dedent(code).strip()
