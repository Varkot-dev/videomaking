"""
CodeTemplate — generates ManimGL scenes for code/pseudocode displays.
Beat types: reveal_lines, highlight_line, dim_others, transition.
"""
from manimgen.templates.base import TemplateScene, normalize_color


def _escape_for_python_str(text: str) -> str:
    """Escape a string value for embedding inside a double-quoted Python string.
    Order matters: backslashes must be escaped BEFORE quotes."""
    return text.replace("\\", "\\\\").replace('"', '\\"')


class CodeTemplate(TemplateScene):

    def scene_setup(self) -> list[str]:
        return ["_code_lines = None", ""]

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "reveal_lines":
            return self._reveal_lines(beat)
        if t == "highlight_line":
            return self._highlight_line(beat)
        if t == "dim_others":
            return self._dim_others(beat)
        if t == "transition":
            return self._transition(beat)
        return []

    def _reveal_lines(self, beat: dict) -> list[str]:
        lines_data = beat.get("lines", [])
        duration = beat.get("duration", 3.0)

        lines = ["_line_mobs = ["]
        for entry in lines_data:
            text = entry.get("text", "")
            color = normalize_color(entry.get("color", "WHITE"))
            escaped = _escape_for_python_str(text)
            lines.append(f'    Text("{escaped}", font="Courier New", font_size=22, color={color}),')
        lines += [
            "]",
            "_code_lines = VGroup(*_line_mobs).arrange(DOWN, aligned_edge=LEFT, buff=0.18)",
            "_code_lines.move_to(ORIGIN + DOWN * 0.3)",
            f"self.play(LaggedStart(*[FadeIn(l) for l in _code_lines], lag_ratio=0.15), run_time={duration})",
            "self.wait(1.0)",
        ]
        return lines

    def _highlight_line(self, beat: dict) -> list[str]:
        index = beat.get("index", 0)
        annotation = beat.get("annotation", "")
        escaped = _escape_for_python_str(annotation)
        return [
            f"_hl_rect = SurroundingRectangle(_code_lines[{index}], color=YELLOW, buff=0.1)",
            f'_hl_ann = Text("\u2190 {escaped}", font_size=22, color=YELLOW)',
            "_hl_ann.next_to(_hl_rect, RIGHT, buff=0.2)",
            "self.play(ShowCreation(_hl_rect), Write(_hl_ann))",
            "self.wait(2.0)",
        ]

    def _dim_others(self, beat: dict) -> list[str]:
        keep = beat.get("keep_index", 0)
        return [
            f"self.play(*[l.animate.set_opacity(0.3) for i, l in enumerate(_code_lines) if i != {keep}])",
            "self.wait(1.5)",
            "self.play(*[l.animate.set_opacity(1.0) for l in _code_lines])",
        ]

    def _transition(self, beat: dict) -> list[str]:
        lines = [
            "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.5)",
            "_code_lines = None",
            "self.wait(0.2)",
        ]
        self._last_beat_cleared_scene = True
        return lines
