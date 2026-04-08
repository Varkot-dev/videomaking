from manimlib import *


class ColorFillScene(Scene):
    """
    techniques: color_fill, brace_annotation
    Pattern: Filled area under a curve — used for integrals, probability
    regions, or any "area between" concept.

    Key techniques demonstrated:
    - axes.get_area(curve, x_range=[a, b], color=BLUE, opacity=0.35)
    - Animating the fill appearing with FadeIn
    - Brace + Text label for annotating a region
    - Two fills on the same axes to show "before vs after" regions

    Use when narration says "the area under", "accumulate", "integrate",
    "region between", or "shade".
    """

    def construct(self):
        title = Text("Area under a curve", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)

        axes = Axes(
            x_range=[0, 4, 1],
            y_range=[0, 5, 1],
            width=8,
            height=4.5,
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
            y_axis_config={"include_numbers": False},
        ).center().shift(DOWN * 0.8)

        curve = axes.get_graph(lambda x: 0.5 * x ** 2 + 0.5, color=YELLOW, x_range=[0, 3.8])
        curve_label = Tex(r"f(x) = \tfrac{1}{2}x^2 + \tfrac{1}{2}", color=YELLOW).scale(0.85)
        curve_label.next_to(axes, RIGHT, buff=0.3).shift(UP * 1.5)

        self.play(ShowCreation(axes), run_time=1.0)
        self.play(ShowCreation(curve), Write(curve_label), run_time=1.5)
        self.wait(0.5)

        # Fill region [0, 2] in blue
        area_left = axes.get_area(curve, x_range=[0, 2], color=BLUE, opacity=0.35)
        self.play(FadeIn(area_left), run_time=1.0)

        # Brace underneath to label the interval
        brace = Brace(
            Line(axes.c2p(0, 0), axes.c2p(2, 0)),
            direction=DOWN,
            buff=0.1,
            color=BLUE,
        )
        brace_label = Text("x ∈ [0, 2]", font_size=28, color=BLUE).next_to(brace, DOWN, buff=0.15)
        self.play(GrowFromCenter(brace), Write(brace_label), run_time=0.8)
        self.wait(0.8)

        # Extend fill to [2, 3.5] in a different colour to show "adding more"
        area_right = axes.get_area(curve, x_range=[2, 3.5], color=GREEN, opacity=0.35)
        self.play(FadeIn(area_right), run_time=0.8)

        total_label = Text("Total area grows", font_size=28, color=GREEN)
        total_label.next_to(axes, RIGHT, buff=0.3).shift(DOWN * 0.5)
        self.play(Write(total_label), run_time=0.6)
        self.wait(1.0)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
