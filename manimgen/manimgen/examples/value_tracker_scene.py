from manimlib import *


class ValueTrackerScene(Scene):
    """
    techniques: tracker_label
    Pattern: ValueTracker driving multiple always_redraw objects simultaneously —
    a dot on a curve, a vertical line, and a coordinate label all staying in sync.

    Key lessons:
    - always_redraw() closures capture the tracker by reference — must use get_value()
    - Coordinate label updated via always_redraw Tex — use f-string with get_value()
    - Multiple always_redraw objects driven by one tracker stay perfectly in sync
    - Run all always_redraw objects in one self.play() with a single tracker.animate
    """

    def construct(self):
        axes = Axes(
            x_range=[-3, 3, 1],
            y_range=[-2, 5, 1],
            width=9,
            height=6,
            axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}, "color": GREY_B},
        ).center().shift(DOWN * 0.3)

        x_label = Text("x", font_size=28).next_to(axes.x_axis, RIGHT, buff=0.2)
        y_label = Text("f(x)", font_size=28).next_to(axes.y_axis, UP, buff=0.2)
        self.play(ShowCreation(axes), Write(x_label), Write(y_label))
        self.wait(0.3)

        curve = axes.get_graph(lambda x: x**2 - 1, color=YELLOW, x_range=[-2.2, 2.2])
        self.play(ShowCreation(curve), run_time=1.5)
        self.wait(0.3)

        t = ValueTracker(-2.2)

        # Dot on the curve
        dot = always_redraw(lambda: Dot(
            axes.input_to_graph_point(t.get_value(), curve), color=RED, radius=0.1
        ))

        # Vertical line from x-axis to dot
        v_line = always_redraw(lambda: DashedLine(
            axes.c2p(t.get_value(), 0),
            axes.input_to_graph_point(t.get_value(), curve),
            dash_length=0.1, color=GREY_B, stroke_width=2,
        ))

        # Coordinate label tracking the dot — clamped to stay in frame
        coord_label = always_redraw(lambda: Tex(
            rf"({t.get_value():.1f},\ {t.get_value()**2 - 1:.1f})",
            font_size=28, color=WHITE
        ).next_to(
            axes.input_to_graph_point(t.get_value(), curve), UP, buff=0.2
        ))

        self.play(FadeIn(dot), FadeIn(v_line), FadeIn(coord_label))
        self.wait(0.3)

        # Sweep right across the curve
        self.play(t.animate.set_value(2.2), run_time=4.0, rate_func=linear)
        self.wait(0.5)

        # Sweep back to minimum
        self.play(t.animate.set_value(0.0), run_time=1.5, rate_func=smooth)
        self.wait(0.5)

        # Highlight the minimum
        min_label = Tex(r"\text{minimum at } x=0", font_size=32, color=YELLOW)
        min_label.next_to(axes, RIGHT, buff=0.5).shift(UP * 0.5)
        self.play(Write(min_label))
        self.wait(1.0)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.5)
