from manimlib import *


class ValueTrackerTracerScene(Scene):
    """
    techniques: value_tracker_tracer
    Pattern: ValueTracker drives a dot and label along a parametric curve via always_redraw.

    Key techniques demonstrated:
    - ValueTracker(start) for continuous parameter control
    - always_redraw(lambda: Dot(axes.input_to_graph_point(t.get_value(), curve))) for live dot
    - always_redraw label that follows the dot
    - self.play(t.animate.set_value(end), run_time=4, rate_func=linear) for smooth traversal
    - Vertical dashed line from curve point to x-axis (also always_redraw)

    Use when narration involves "as x increases", "tracing the function", "the derivative at each
    point", "following the value", "sweep from left to right along the curve".
    """

    def construct(self):
        title = Text("Tracing a Curve", font_size=44).to_edge(UP, buff=0.6)
        self.play(Write(title), run_time=0.8)

        axes = Axes(
            x_range=[-0.5, TAU + 0.3, PI / 2],
            y_range=[-1.4, 1.4, 0.5],
            width=9,
            height=4.5,
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={"include_numbers": False},
            y_axis_config={"include_numbers": False},
        ).center().shift(DOWN * 0.5)

        # x-axis tick labels at multiples of π/2
        x_labels = VGroup(
            Text("π/2", font_size=20, color=GREY_A).move_to(axes.c2p(PI / 2, 0) + DOWN * 0.35),
            Text("π",   font_size=20, color=GREY_A).move_to(axes.c2p(PI, 0)     + DOWN * 0.35),
            Text("3π/2",font_size=20, color=GREY_A).move_to(axes.c2p(3*PI/2, 0) + DOWN * 0.35),
            Text("2π",  font_size=20, color=GREY_A).move_to(axes.c2p(TAU, 0)    + DOWN * 0.35),
        )

        curve = axes.get_graph(lambda x: np.sin(x), color=BLUE, x_range=[0, TAU])

        self.play(ShowCreation(axes), Write(x_labels), run_time=1.0)
        self.play(ShowCreation(curve), run_time=1.5)

        # ValueTracker driving the tracer
        t = ValueTracker(0.0)

        dot = always_redraw(lambda: Dot(
            axes.input_to_graph_point(t.get_value(), curve),
            color=RED, radius=0.12,
        ))

        coord_label = always_redraw(lambda: Text(
            f"sin({t.get_value():.2f}) = {np.sin(t.get_value()):.2f}",
            font_size=24, color=WHITE,
        ).next_to(dot, UP, buff=0.25))

        v_line = always_redraw(lambda: DashedLine(
            axes.c2p(t.get_value(), 0),
            axes.input_to_graph_point(t.get_value(), curve),
            dash_length=0.1, color=YELLOW, stroke_width=1.5,
        ))

        self.add(v_line, dot, coord_label)
        self.play(FadeIn(dot), run_time=0.4)

        # Sweep across the full curve
        self.play(t.animate.set_value(TAU), run_time=5.0, rate_func=linear)
        self.wait(0.8)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
