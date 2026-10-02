from manimlib import *


class EpsilonDeltaScene(Scene):
    """
    techniques: tracker_label, brace_annotation
    Pattern: Epsilon-delta definition — two shrinking bands (horizontal epsilon, vertical delta)
    centered on a limit point, animated via ValueTrackers.

    Key lessons:
    - Use Rectangle for bands, centered via .move_to(axes.c2p(...))
    - always_redraw() + ValueTracker to animate bands shrinking
    - Brace + label for epsilon/delta annotations on the axes
    - Color coding: epsilon band = blue (output), delta band = yellow (input)
    """

    def construct(self):
        title = Text("How Close is Close Enough?", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.3)

        axes = Axes(
            x_range=[-0.5, 4, 1],
            y_range=[-0.5, 4, 1],
            width=6,
            height=5,
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
            y_axis_config={"include_numbers": False},
        ).center().shift(DOWN * 0.8 + LEFT * 1.0)

        y_labels = VGroup(*[
            Text(str(n), font_size=22, color=GREY_A).next_to(axes.y_axis.n2p(n), LEFT, buff=0.15)
            for n in [1, 2, 3]
        ])
        x_name = Text("x", font_size=26).next_to(axes.x_axis, RIGHT, buff=0.15)
        y_name = Text("f(x)", font_size=26).next_to(axes.y_axis, UP, buff=0.15)
        self.play(ShowCreation(axes), FadeIn(y_labels), Write(x_name), Write(y_name))
        self.wait(0.3)

        # Continuous curve y = x (limit at x=2 is L=2)
        curve = axes.get_graph(lambda x: x, color=TEAL, x_range=[0.05, 3.45])
        self.play(ShowCreation(curve), run_time=1.5)
        self.wait(0.3)

        # Mark the limit point
        limit_dot = Dot(axes.c2p(2, 2), color=WHITE, radius=0.09)
        self.play(FadeIn(limit_dot))
        self.wait(0.2)

        # Labels for a and L on the axes
        a_label = Text("a=2", font_size=24, color=YELLOW).next_to(axes.c2p(2, 0), DOWN, buff=0.2)
        L_label = Text("L=2", font_size=24, color=BLUE_B).next_to(axes.c2p(0, 2), LEFT, buff=0.2)
        self.play(Write(a_label), Write(L_label))
        self.wait(0.3)

        # Epsilon and delta trackers — start large, then shrink
        eps = ValueTracker(1.2)
        delta = ValueTracker(1.2)

        # Epsilon band: horizontal rectangle on the y-axis side (output range)
        eps_band = always_redraw(lambda: Rectangle(
            width=axes.x_length + 0.2,
            height=axes.y_axis.n2p(eps.get_value())[1] - axes.y_axis.n2p(0)[1],
            fill_color=BLUE,
            fill_opacity=0.2,
            stroke_width=0,
        ).move_to(axes.c2p(-0.25 + (3.5 / 2), 2)))

        # Delta band: vertical rectangle on the x-axis side (input range)
        delta_band = always_redraw(lambda: Rectangle(
            width=axes.x_axis.n2p(delta.get_value())[0] - axes.x_axis.n2p(0)[0],
            height=axes.y_length + 0.2,
            fill_color=YELLOW,
            fill_opacity=0.2,
            stroke_width=0,
        ).move_to(axes.c2p(2, -0.25 + (3.5 / 2))))

        eps_label = always_redraw(lambda: Tex(
            rf"\varepsilon = {eps.get_value():.1f}", font_size=28, color=BLUE_B
        ).next_to(axes, RIGHT, buff=0.5).shift(UP * 1.2))

        delta_label = always_redraw(lambda: Tex(
            rf"\delta = {delta.get_value():.1f}", font_size=28, color=YELLOW
        ).next_to(axes, RIGHT, buff=0.5).shift(UP * 0.4))

        self.play(FadeIn(eps_band), FadeIn(delta_band), Write(eps_label), Write(delta_label))
        self.wait(0.8)

        # Shrink both bands — the key insight
        self.play(
            eps.animate.set_value(0.5),
            delta.animate.set_value(0.5),
            run_time=2.5, rate_func=smooth,
        )
        self.wait(0.5)
        self.play(
            eps.animate.set_value(0.15),
            delta.animate.set_value(0.15),
            run_time=2.5, rate_func=smooth,
        )
        self.wait(1.0)

        conclusion = Tex(
            r"\text{No matter how small } \varepsilon, \text{ we find a } \delta",
            font_size=28, color=WHITE
        ).next_to(axes, RIGHT, buff=0.5).shift(DOWN * 0.5)
        self.play(Write(conclusion))
        self.wait(1.5)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.5)
