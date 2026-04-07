"""
ProbabilityTemplate — generates ManimGL scenes for probability visualizations.
Beat types: bar_chart, highlight_bar, sample_space, annotation.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class ProbabilityTemplate(TemplateScene):

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "bar_chart":
            return self._bar_chart(beat)
        if t == "highlight_bar":
            return self._highlight_bar(beat)
        if t == "sample_space":
            return self._sample_space(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def _bar_chart(self, beat: dict) -> list[str]:
        categories = beat.get("categories", [])
        values = beat.get("values", [])
        colors = beat.get("colors", [])
        color_list = [normalize_color(c) for c in colors]
        color_list += ["BLUE"] * (len(values) - len(color_list))
        shift = ".shift(DOWN * 0.5)" if self.title else ""
        return [
            f"_bar_values = {values}",
            f"_bar_colors = [{', '.join(color_list)}]",
            # ManimGL BarChart API: values, max_value, bar_names, bar_colors, height, width
            "bc = BarChart(",
            "    _bar_values,",
            f"    bar_names={categories},",
            "    bar_colors=_bar_colors,",
            "    max_value=max(_bar_values),",
            "    height=4,",
            "    width=8,",
            ")",
            f"bc.center(){shift}",
            "self.play(ShowCreation(bc))",
            "self.wait(0.5)",
        ]

    def _highlight_bar(self, beat: dict) -> list[str]:
        index = beat.get("index", 0)
        color = beat.get("color", "YELLOW")
        return [
            f"self.play(bc.bars[{index}].animate.set_color({color}))",
            "self.wait(0.8)",
        ]

    def _sample_space(self, beat: dict) -> list[str]:
        regions = beat.get("regions", [])
        lines = [
            "_ss_rects = VGroup()",
            "_ss_labels = VGroup()",
            f"_ss_regions = {regions}",
            "_ss_x = -4.0",
            "for _ss_r in _ss_regions:",
            "    _ss_w = _ss_r['probability'] * 8.0",
            "    _ss_rect = Rectangle(width=_ss_w, height=2.0)",
            "    _ss_rect.set_fill(color=_ss_r['color'], opacity=0.7)",
            "    _ss_rect.set_stroke(color=WHITE, width=1)",
            "    _ss_rect.move_to(np.array([_ss_x + _ss_w/2, 0, 0]))",
            "    _ss_x += _ss_w",
            "    _ss_rects.add(_ss_rect)",
            "    _ss_lbl = Text(_ss_r['label'], font_size=22, color=WHITE)",
            "    _ss_lbl.move_to(_ss_rect.get_center())",
            "    _ss_labels.add(_ss_lbl)",
            "self.play(ShowCreation(_ss_rects))",
            "self.play(LaggedStart(*[Write(l) for l in _ss_labels], lag_ratio=0.2))",
            "self.wait(1.0)",
        ]
        return lines

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        escaped = text.replace('"', '\\"')
        return [
            f'_prob_ann = Text("{escaped}", font_size=28, color=WHITE)',
            "_prob_ann.to_edge(DOWN, buff=0.5)",
            "self.play(Write(_prob_ann))",
            "self.wait(1.0)",
        ]
