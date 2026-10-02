from manimlib import *


class GraphScene(Scene):
    """
    techniques: axes_curve, tracker_label
    Pattern: Graph plotting with title — axes, function curve, external labels, dynamic tracing.

    Key layout rules demonstrated here:
    - Title at top via to_edge(UP, buff=0.8); axes shifted DOWN * 0.5 to avoid collision.
    - Axes always sized with .set_width(10) so they never overflow the frame.
    - axis_config always includes decimal_number_config={"font_size": 24} so tick labels don't render oversized.
    - Graph labels placed outside the axes area with .next_to(axes, RIGHT, buff=0.5).
    - Multiple annotations grouped in VGroup + .arrange(DOWN) — never independently next_to same anchor.
    - Clean FadeOut of all objects at the end.

    Covers: Text, Axes, get_graph, get_graph_label, ShowCreation, ValueTracker,
            always_redraw, Dot, VGroup, Arrow, FadeIn, FadeOut.
    """

    def construct(self):
        # --- Title ---
        title = Text("Graphing Functions", font_size=56).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.5)

        # --- Axes: always set_width, always shift down when title is present ---
        axes = Axes(
            x_range=[-3, 3, 1],
            y_range=[-1, 5, 1],
            axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}, "color": GREY_B},
        ).set_width(10).center().shift(DOWN * 0.5)

        x_label = Text("x", font_size=28).next_to(axes.x_axis, RIGHT, buff=0.2)
        y_label = Text("f(x)", font_size=28).next_to(axes.y_axis, UP, buff=0.2)

        self.play(ShowCreation(axes), Write(x_label), Write(y_label))
        self.wait(0.5)

        # --- Plot x^2 ---
        parabola = axes.get_graph(lambda x: x ** 2, color=YELLOW, x_range=[-2.2, 2.2])

        # Label placed OUTSIDE axes region — never inside or on top of the curve
        parabola_label = Tex(r"f(x) = x^2", font_size=32, color=YELLOW)
        parabola_label.next_to(axes, RIGHT, buff=0.4).shift(UP * 1.0)

        self.play(ShowCreation(parabola), run_time=2.0)
        self.play(Write(parabola_label))
        self.wait(1.0)

        # --- Trace a moving dot along the parabola ---
        t = ValueTracker(-2.2)
        dot = always_redraw(
            lambda: Dot(axes.input_to_graph_point(t.get_value(), parabola), color=RED, radius=0.08)
        )

        self.play(FadeIn(dot))
        self.play(t.animate.set_value(2.2), run_time=3.0, rate_func=linear)
        self.wait(0.8)

        # --- Transition: swap to a second curve ---
        self.play(FadeOut(parabola), FadeOut(parabola_label), FadeOut(dot))

        linear_curve = axes.get_graph(lambda x: 2 * x - x ** 2 + 1, color=TEAL, x_range=[-0.5, 2.5])

        # Two annotations for the new curve — grouped so they don't overlap
        annotation = VGroup(
            Tex(r"g(x) = 2x - x^2 + 1", font_size=28, color=TEAL),
            Tex(r"\text{max at } x=1", font_size=24, color=GREY_A),
        ).arrange(DOWN, buff=0.35, aligned_edge=LEFT)
        annotation.next_to(axes, RIGHT, buff=0.4).shift(UP * 0.5)

        self.play(ShowCreation(linear_curve), run_time=2.0)
        self.play(Write(annotation))
        self.wait(1.0)

        # --- Clean exit ---
        self.play(
            FadeOut(title), FadeOut(axes), FadeOut(x_label), FadeOut(y_label),
            FadeOut(linear_curve), FadeOut(annotation),
            run_time=0.8,
        )
        self.wait(0.5)
