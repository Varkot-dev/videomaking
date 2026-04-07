"""
NumberLineTemplate — generates ManimGL scenes for number line visualizations.
Beat types: line_appear, mark_point, mark_interval, jump_arrow, annotation.
"""
from manimgen.templates.base import TemplateScene


class NumberLineTemplate(TemplateScene):

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "line_appear":
            return self._line_appear(beat)
        if t == "mark_point":
            return self._mark_point(beat)
        if t == "mark_interval":
            return self._mark_interval(beat)
        if t == "jump_arrow":
            return self._jump_arrow(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def _line_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [-5, 5, 1])
        include_numbers = beat.get("include_numbers", True)
        shift = ".shift(DOWN * 0.5)" if self.title else ""
        return [
            "nl = NumberLine(",
            f"    x_range={x_range},",
            f"    include_numbers={include_numbers},",
            "    numbers_with_elongated_ticks=[0],",
            ")",
            f"nl.center(){shift}",
            "self.play(ShowCreation(nl))",
            "self.wait(0.5)",
        ]

    def _mark_point(self, beat: dict) -> list[str]:
        value = beat.get("value", 0)
        color = beat.get("color", "YELLOW")
        label = beat.get("label", str(value))
        escaped = label.replace('"', '\\"')
        return [
            f"_mp_dot = Dot(nl.n2p({value}), color={color}, radius=0.12)",
            f'_mp_label = Text("{escaped}", font_size=28, color={color})',
            "_mp_label.next_to(_mp_dot, UP, buff=0.2)",
            "self.play(GrowFromCenter(_mp_dot), Write(_mp_label))",
            "self.wait(0.5)",
        ]

    def _mark_interval(self, beat: dict) -> list[str]:
        start = beat.get("start", 0)
        end = beat.get("end", 1)
        color = beat.get("color", "GREEN")
        return [
            f"_mi_line = Line(nl.n2p({start}), nl.n2p({end}), color={color}, stroke_width=8)",
            "self.play(ShowCreation(_mi_line))",
            "self.wait(0.5)",
        ]

    def _jump_arrow(self, beat: dict) -> list[str]:
        from_val = beat.get("from_val", 0)
        to_val = beat.get("to_val", 1)
        label = beat.get("label", None)
        mid = (from_val + to_val) / 2
        lines = [
            f"_ja_start = nl.n2p({from_val})",
            f"_ja_end = nl.n2p({to_val})",
            f"_ja_mid = nl.n2p({mid}) + UP * 0.8",
            "_ja_arrow = CurvedArrow(_ja_start, _ja_end, angle=TAU/6, color=WHITE)",
            "self.play(ShowCreation(_ja_arrow))",
        ]
        if label:
            escaped = label.replace('"', '\\"')
            lines += [
                f'_ja_label = Text("{escaped}", font_size=24, color=WHITE)',
                "_ja_label.move_to(_ja_mid + UP * 0.3)",
                "self.play(Write(_ja_label))",
            ]
        lines.append("self.wait(0.5)")
        return lines

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        escaped = text.replace('"', '\\"')
        return [
            f'_nl_ann = Text("{escaped}", font_size=28, color=WHITE)',
            "_nl_ann.to_edge(DOWN, buff=0.6)",
            "self.play(Write(_nl_ann))",
            "self.wait(1.0)",
        ]
