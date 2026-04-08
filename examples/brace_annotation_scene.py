from manimlib import *


class BraceAnnotationScene(Scene):
    """
    techniques: brace_annotation
    Demonstrates Brace with label for annotating spans, distances, and intervals.

    KEY RULES shown here:
    - Brace(obj, direction=DOWN/UP/LEFT/RIGHT) wraps any Mobject
    - Always place label with brace_label.next_to(brace, direction, buff=0.2)
    - Group brace + label as VGroup for coordinated animations
    - For multiple annotations on same axis: VGroup(...).arrange() — never two next_to() same anchor
    - GrowFromCenter(brace) is the correct intro animation for Brace
    """

    def construct(self):
        # CUE 0 — 3.5s: Show a number line with a marked interval
        title = Text("Interval Notation", font_size=34, color=WHITE).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=0.6)

        number_line = NumberLine(
            x_range=[-1, 7, 1],
            length=10,
            include_numbers=True,
            decimal_number_config={"font_size": 24},
            color=GREY_B,
        ).center().shift(DOWN * 0.3)

        self.play(ShowCreation(number_line), run_time=1.0)
        self.wait(3.5 - 0.6 - 1.0)  # 1.9s ✓

        # CUE 1 — 4.0s: Mark a segment [2, 5] and annotate with a brace
        dot_a = Dot(number_line.n2p(2), color=YELLOW, radius=0.1)
        dot_b = Dot(number_line.n2p(5), color=YELLOW, radius=0.1)
        segment = Line(number_line.n2p(2), number_line.n2p(5),
                       color=YELLOW, stroke_width=5)

        self.play(
            ShowCreation(segment),
            FadeIn(dot_a),
            FadeIn(dot_b),
            run_time=0.6,
        )

        # Brace below the segment
        brace = Brace(segment, direction=DOWN, buff=0.15, color=WHITE)
        brace_label = Tex(r"[2,\ 5]", font_size=36, color=WHITE)
        brace_label.next_to(brace, DOWN, buff=0.2)

        self.play(GrowFromCenter(brace), run_time=0.5)
        self.play(FadeIn(brace_label), run_time=0.4)
        self.wait(4.0 - 0.6 - 0.5 - 0.4)  # 2.5s ✓

        # CUE 2 — 4.5s: Annotate the length separately with a brace above
        length_brace = Brace(segment, direction=UP, buff=0.15, color=TEAL_C)
        length_label = Tex(r"\text{length} = 3", font_size=32, color=TEAL_C)
        length_label.next_to(length_brace, UP, buff=0.2)

        # \text{} mid-expression is fine — this is valid amsmath usage
        self.play(GrowFromCenter(length_brace), run_time=0.5)
        self.play(Write(length_label), run_time=0.7)
        self.wait(4.5 - 0.5 - 0.7)  # 3.3s ✓

        # CUE 3 — 2.0s: FadeOut
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(1.2)
