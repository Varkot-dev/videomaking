"""
FunctionTemplate — generates ManimGL scenes for function graphing.
Beat types: axes_appear, curve_appear, trace_dot, annotation, transition.
"""
from manimgen.templates.base import TemplateScene, normalize_color


class FunctionTemplate(TemplateScene):

    def __init__(self, spec: dict):
        super().__init__(spec)
        self._curve_count = 0        # for unique variable names per curve_appear beat
        self._last_curve_var = None  # tracks the most recent curve variable for trace_dot

    def render_beat(self, beat: dict) -> list[str]:
        t = beat.get("type")
        if t == "axes_appear":
            return self._axes_appear(beat)
        if t == "curve_appear":
            return self._curve_appear(beat)
        if t == "trace_dot":
            return self._trace_dot(beat)
        if t == "annotation":
            return self._annotation(beat)
        if t == "transition":
            return self._transition(beat)
        return []

    def _axes_appear(self, beat: dict) -> list[str]:
        x_range = beat.get("x_range", [-3, 3, 1])
        y_range = beat.get("y_range", [-1, 5, 1])
        x_label = beat.get("x_label", "x")
        y_label = beat.get("y_label", "f(x)")
        shift = ".shift(DOWN * 0.5)" if self.title else ""
        return [
            "axes = Axes(",
            f"    x_range={x_range},",
            f"    y_range={y_range},",
            '    axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}, "color": GREY_B},',
            f").set_width(10).center(){shift}",
            f'x_label = Text("{x_label}", font_size=28).next_to(axes.x_axis, RIGHT, buff=0.2)',
            f'y_label = Text("{y_label}", font_size=28).next_to(axes.y_axis, UP, buff=0.2)',
            "self.play(ShowCreation(axes), Write(x_label), Write(y_label))",
            "self.wait(0.5)",
        ]

    def _curve_appear(self, beat: dict) -> list[str]:
        expr = beat.get("expr_str", "x**2")
        color = normalize_color(beat.get("color", "YELLOW"))
        x_range = beat.get("x_range", None)
        label_text = beat.get("label", "")

        # Unique variable name per beat to avoid shadowing
        var_name = f"curve_{self._curve_count}"
        self._curve_count += 1
        self._last_curve_var = var_name

        x_range_arg = f", x_range={x_range}" if x_range else ""
        lines = [
            f"{var_name} = axes.get_graph(lambda x: {expr}, color={color}{x_range_arg})",
        ]
        if label_text:
            escaped = label_text.replace('"', '\\"')
            lines += [
                f'{var_name}_label = Tex(r"{escaped}", color={color})',
                f"{var_name}_label.next_to(axes, RIGHT, buff=0.4).shift(UP * 1.0)",
                f"self.play(ShowCreation({var_name}), run_time=2.0)",
                f"self.play(Write({var_name}_label))",
                "self.wait(0.5)",
            ]
        else:
            lines += [
                f"self.play(ShowCreation({var_name}), run_time=2.0)",
                "self.wait(0.5)",
            ]
        return lines

    def _trace_dot(self, beat: dict) -> list[str]:
        start_x = beat.get("start_x", -2.0)
        end_x = beat.get("end_x", 2.0)
        color = normalize_color(beat.get("color", "RED"))
        duration = beat.get("duration", 3.0)
        if self._last_curve_var is None:
            # No curve defined yet — emit a no-op comment so the scene still parses
            return ["# trace_dot skipped: no curve_appear beat has run yet"]
        curve_var = self._last_curve_var
        return [
            f"_t = ValueTracker({start_x})",
            "_dot = always_redraw(",
            f"    lambda: Dot(axes.input_to_graph_point(_t.get_value(), {curve_var}), color={color}, radius=0.08)",
            ")",
            "self.play(FadeIn(_dot))",
            f"self.play(_t.animate.set_value({end_x}), run_time={duration}, rate_func=linear)",
            "self.wait(0.5)",
            "self.play(FadeOut(_dot))",
        ]

    def _annotation(self, beat: dict) -> list[str]:
        text = beat.get("text", "")
        position = beat.get("position", "right")
        color = normalize_color(beat.get("color", "WHITE"))
        escaped = text.replace('"', '\\"')
        # Use static positions — never call .to_edge() on axes (that mutates/moves it)
        pos_map = {
            "right": "RIGHT * 5.5",
            "left": "LEFT * 5.5",
            "top": "UP * 3.2",
        }
        pos = pos_map.get(position, "RIGHT * 5.5")
        return [
            f'_ann = Tex(r"{escaped}", color={color})',
            f"_ann.move_to({pos})",
            "self.play(Write(_ann))",
            "self.wait(1.0)",
        ]

    def _transition(self, beat: dict) -> list[str]:
        # Keep title if present; fade everything else
        if self.title:
            lines = [
                "self.play(*[FadeOut(m) for m in self.mobjects if m is not title], run_time=0.6)",
                "self.wait(0.2)",
            ]
        else:
            lines = [
                "self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.6)",
                "self.wait(0.2)",
            ]
        # Reset curve counter so next set of beats starts fresh
        self._curve_count = 0
        self._last_curve_var = None
        return lines
