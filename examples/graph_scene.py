from manimlib import *


class GraphScene(Scene):
    """
    Pattern: Graph plotting — axes, function curve, labels, dynamic tracing.
    Covers: Axes, get_graph, get_graph_label, ShowCreation, ValueTracker,
            always_redraw, Dot, DashedLine.
    """

    def construct(self):
        # --- Build axes ---
        axes = Axes(
            x_range=[-3, 3, 1],
            y_range=[-1, 5, 1],
            axis_config={"include_numbers": True, "color": GREY_B},
        )
        axes.move_to(ORIGIN)

        x_label = Text("x", font_size=28).next_to(axes.x_axis, RIGHT)
        y_label = Text("f(x)", font_size=28).next_to(axes.y_axis, UP)

        self.play(ShowCreation(axes), Write(x_label), Write(y_label))
        self.wait(0.5)

        # --- Plot x^2 ---
        parabola = axes.get_graph(lambda x: x ** 2, color=YELLOW, x_range=[-2.2, 2.2])
        parabola_label = axes.get_graph_label(parabola, label="x^2", x=1.8, color=YELLOW)

        self.play(ShowCreation(parabola), run_time=2)
        self.play(Write(parabola_label))
        self.wait(1)

        # --- Plot sin(x) on top ---
        sin_curve = axes.get_graph(lambda x: 2 * x - x ** 2 + 1, color=TEAL, x_range=[-0.5, 2.5])
        sin_label = axes.get_graph_label(sin_curve, label="2x - x^2 + 1", x=0.5, color=TEAL)

        self.play(ShowCreation(sin_curve), run_time=2)
        self.play(Write(sin_label))
        self.wait(1)

        # --- Trace a moving dot along the parabola ---
        t = ValueTracker(-2.2)
        dot = always_redraw(
            lambda: Dot(axes.input_to_graph_point(t.get_value(), parabola), color=RED, radius=0.1)
        )

        self.play(FadeIn(dot))
        self.play(t.animate.set_value(2.2), run_time=3, rate_func=linear)
        self.wait(1)

        # --- Clean exit ---
        self.play(FadeOut(axes), FadeOut(parabola), FadeOut(parabola_label),
                  FadeOut(sin_curve), FadeOut(sin_label), FadeOut(dot),
                  FadeOut(x_label), FadeOut(y_label))
        self.wait(0.5)
