"""
LimitTemplate — generates ManimGL scenes for limit demonstrations.
Beat types: axes_appear, curve_appear, guide_lines, approach_dot, annotation.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class LimitTemplate(TemplateScene):

    def __init__(self, spec: dict):
        super().__init__(spec)
        # Remember hole position from curve_appear so approach_dot can use it
        self._hole_x: float = 1.0
        self._curve_x_start: float = 0.05
        self._curve_x_end: float = 3.0

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "axes_appear":
            return self._axes_appear(beat)
        if t == "curve_appear":
            return self._curve_appear(beat)
        if t == "guide_lines":
            return self._guide_lines(beat)
        if t == "approach_dot":
            return self._approach_dot(beat)
        if t == "annotation":
            return self._annotation(beat)
        return []

    def _axes_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [0, 4, 1])
        y_range = beat.get("y_range", [0, 4, 1])
        shift = ".shift(DOWN * 1.2 + LEFT * 0.5)" if self.title else ".center()"
        return [
            "axes = Axes(",
            f"    x_range={x_range},",
            f"    y_range={y_range},",
            '    axis_config={"color": GREY_B, "include_tip": True},',
            '    x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},',
            '    y_axis_config={"include_numbers": False},',
            f").set_width(6).center(){shift}",
            "y_labels = VGroup()",
            f"for n in range(int({y_range[0]})+1, int({y_range[1]})):",
            "    lbl = Text(str(n), font_size=22, color=GREY_A)",
            "    lbl.next_to(axes.y_axis.n2p(n), LEFT, buff=0.15)",
            "    y_labels.add(lbl)",
            'x_name = Text("x", font_size=26).next_to(axes.x_axis, RIGHT, buff=0.15)',
            'y_name = Text("f(x)", font_size=26).next_to(axes.y_axis, UP, buff=0.15)',
            "self.play(ShowCreation(axes), FadeIn(y_labels), Write(x_name), Write(y_name))",
            "self.wait(0.3)",
        ]

    def _curve_appear(self, beat: dict) -> list[str]:
        expr = beat.get("expr_str", "x + 1")
        color = normalize_color(beat.get("color", "BLUE"))
        hole_x = float(beat.get("hole_x", 1.0))
        hole_y = float(beat.get("hole_y", 2.0))
        gap = 0.08
        # Store for use by approach_dot beat
        self._hole_x = hole_x
        # Derive safe x bounds from the axes_appear beat's x_range if available
        axes_beat = next((b for b in self.beats if b.get("type") == "axes_appear"), {})
        x_range = axes_beat.get("x_range", [0, 4, 1])
        x_start = float(x_range[0]) + 0.05
        x_end = float(x_range[1]) - 0.05
        self._curve_x_start = x_start
        self._curve_x_end = x_end
        return [
            f"_curve_left = axes.get_graph(lambda x: {expr}, color={color}, x_range=[{x_start}, {hole_x - gap}])",
            f"_curve_right = axes.get_graph(lambda x: {expr}, color={color}, x_range=[{hole_x + gap}, {x_end}])",
            "self.play(ShowCreation(_curve_left), ShowCreation(_curve_right), run_time=1.5)",
            "self.wait(0.4)",
            "_hole = Circle(radius=0.1, stroke_color=WHITE, fill_color='#1C1C1C', fill_opacity=1.0)",
            f"_hole.move_to(axes.c2p({hole_x}, {hole_y}))",
            "self.play(ShowCreation(_hole))",
            "self.wait(0.3)",
        ]

    def _guide_lines(self, beat: dict) -> list[str]:
        lx = beat.get("limit_x", 1.0)
        ly = beat.get("limit_y", 2.0)
        return [
            f"_h_dash = DashedLine(axes.c2p(0, {ly}), axes.c2p({lx}, {ly}), dash_length=0.12, color=BLUE_B, stroke_width=2)",
            f"_v_dash = DashedLine(axes.c2p({lx}, 0), axes.c2p({lx}, {ly}), dash_length=0.12, color=BLUE_B, stroke_width=2)",
            "self.play(ShowCreation(_h_dash), ShowCreation(_v_dash), run_time=0.8)",
            "self.wait(0.3)",
        ]

    def _approach_dot(self, beat: dict) -> list[str]:
        from_side = beat.get("from_side", "both")
        start_x = float(beat.get("start_x", self._curve_x_start))
        end_x = float(beat.get("end_x", self._curve_x_end))
        color = normalize_color(beat.get("color", "YELLOW"))
        # Approach targets: dot travels to just before / just after the hole.
        # Uses the stored hole_x rather than the non-existent .t_max/.t_min attrs.
        left_target = round(self._hole_x - 0.1, 4)
        right_target = round(self._hole_x + 0.1, 4)
        lines = []
        if from_side in ("left", "both"):
            lines += [
                f"_tl = ValueTracker({start_x})",
                "_dot_l = always_redraw(",
                f"    lambda: Dot(axes.input_to_graph_point(_tl.get_value(), _curve_left), color={color}, radius=0.09)",
                ")",
                "self.play(FadeIn(_dot_l))",
                f"self.play(_tl.animate.set_value({left_target}), run_time=2.0, rate_func=linear)",
                "self.wait(0.2)",
                "self.play(FadeOut(_dot_l))",
            ]
        if from_side in ("right", "both"):
            lines += [
                f"_tr = ValueTracker({end_x})",
                "_dot_r = always_redraw(",
                f"    lambda: Dot(axes.input_to_graph_point(_tr.get_value(), _curve_right), color={color}, radius=0.09)",
                ")",
                "self.play(FadeIn(_dot_r))",
                f"self.play(_tr.animate.set_value({right_target}), run_time=2.0, rate_func=linear)",
                "self.wait(0.2)",
                "self.play(FadeOut(_dot_r))",
            ]
        return lines

    def _annotation(self, beat: dict) -> list[str]:
        val_label = beat.get("value_label", r"y = 2")
        lim_label = beat.get("limit_label", r"\lim_{x \to 1} f(x) = 2")
        return [
            f'_ann = VGroup(',
            f'    Tex(r"{val_label}", color=WHITE),',
            f'    Tex(r"{lim_label}", color=YELLOW),',
            ").arrange(DOWN, buff=0.5, aligned_edge=LEFT)",
            "_ann.move_to(RIGHT * 3.5 + DOWN * 0.3)",
            "self.play(Write(_ann), run_time=1.2)",
            "self.wait(1.2)",
        ]
