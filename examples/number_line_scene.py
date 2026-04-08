from manimlib import *


class NumberLineScene(Scene):
    """
    techniques: number_line, brace_annotation
    Pattern: Number line — intervals, point markers, arrows, labels.
    No axes needed. Good for: sequences, intervals, modular arithmetic, delta-epsilon on 1D.

    Key lessons:
    - NumberLine is standalone (not inside Axes) — place with .center() or .shift()
    - n2p(value) converts a number to screen coordinates on the line
    - Brace() highlights an interval — attach label with .get_tex()
    - CurvedArrow for showing "jumps" between values
    """

    def construct(self):
        title = Text("Intervals on the Number Line", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.3)

        nl = NumberLine(
            x_range=[-1, 6, 1],
            length=10,
            include_numbers=True,
            decimal_number_config={"font_size": 28},
            color=GREY_B,
        ).center().shift(DOWN * 0.5)

        self.play(ShowCreation(nl))
        self.wait(0.3)

        # Mark a point
        point = Dot(nl.n2p(3), color=YELLOW, radius=0.12)
        point_label = Text("a = 3", font_size=30, color=YELLOW).next_to(nl.n2p(3), UP, buff=0.4)
        self.play(FadeIn(point), Write(point_label))
        self.wait(0.3)

        # Delta interval around a
        delta_val = 1.2
        left_pt = Dot(nl.n2p(3 - delta_val), color=RED, radius=0.1)
        right_pt = Dot(nl.n2p(3 + delta_val), color=RED, radius=0.1)

        interval_line = Line(nl.n2p(3 - delta_val), nl.n2p(3 + delta_val), color=RED, stroke_width=6)
        self.play(ShowCreation(interval_line), FadeIn(left_pt), FadeIn(right_pt))
        self.wait(0.3)

        # Brace under the interval with delta label
        brace = Brace(interval_line, DOWN, buff=0.1, color=RED)
        brace_label = brace.get_tex(r"2\delta", font_size=32)
        brace_label.set_color(RED)
        self.play(ShowCreation(brace), Write(brace_label))
        self.wait(0.5)

        # Show a value inside the interval approaching a
        approach_dot = Dot(nl.n2p(1.5), color=BLUE, radius=0.12)
        approach_label = Text("x", font_size=30, color=BLUE).next_to(nl.n2p(1.5), UP, buff=0.4)
        self.play(FadeIn(approach_dot), Write(approach_label))
        self.play(
            approach_dot.animate.move_to(nl.n2p(2.85)),
            approach_label.animate.next_to(nl.n2p(2.85), UP, buff=0.4),
            run_time=2.0, rate_func=smooth,
        )
        self.wait(1.0)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.5)
