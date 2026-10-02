from manimlib import *


class SweepHighlightScene(Scene):
    """
    techniques: sweep_highlight
    Demonstrates scanning a SurroundingRectangle across a sequence of elements.

    KEY RULES shown here:
    - SurroundingRectangle is a Mobject — always wrap in ShowCreation() for self.play()
    - To move+resize the rect: use rect.become(SurroundingRectangle(target, ...))
    - Never use rect.animate.move_to() — that only translates, never resizes
    - Correct pattern for highlighting during narration: ShowCreation then become() loop
    """

    def construct(self):
        # CUE 0 — 3.0s: Show array of boxes with values
        values = [64, 34, 25, 12, 22, 11, 90]
        boxes = VGroup(*[
            Square(side_length=0.8, fill_color="#2a2a2a", fill_opacity=1,
                   stroke_width=2, color=GREY_B)
            for _ in values
        ]).arrange(RIGHT, buff=0.12).center()

        labels = VGroup(*[
            Text(str(v), font_size=28, color=WHITE).move_to(boxes[i])
            for i, v in enumerate(values)
        ])

        title = Text("Bubble Sort", font_size=36, color=WHITE).to_edge(UP, buff=0.8)

        self.play(Write(title), run_time=0.6)
        self.play(LaggedStart(
            *[FadeIn(b) for b in boxes], lag_ratio=0.08
        ), run_time=0.8)
        self.play(LaggedStart(
            *[FadeIn(l) for l in labels], lag_ratio=0.08
        ), run_time=0.6)
        self.wait(1.0)  # 0.6 + 0.8 + 0.6 + 1.0 = 3.0 ✓

        # CUE 1 — 5.0s: Scan rectangle across all elements
        # CORRECT pattern: ShowCreation to introduce, then become() to move+resize
        scan_rect = SurroundingRectangle(boxes[0], color=YELLOW, buff=0.06)
        self.play(ShowCreation(scan_rect), run_time=0.3)

        for i in range(1, len(boxes)):
            self.play(
                scan_rect.become(SurroundingRectangle(boxes[i], color=YELLOW, buff=0.06)),
                run_time=0.25,
            )
        self.wait(5.0 - 0.3 - 0.25 * (len(boxes) - 1))  # fill to 5.0s

        # CUE 2 — 4.0s: Highlight a swap — two boxes change color
        swap_i, swap_j = 0, 1
        self.play(
            boxes[swap_i].animate.set_color(RED),
            boxes[swap_j].animate.set_color(RED),
            run_time=0.4,
        )
        # Animate the values swapping positions
        self.play(
            labels[swap_i].animate.move_to(boxes[swap_j]),
            labels[swap_j].animate.move_to(boxes[swap_i]),
            run_time=0.6,
        )
        self.play(
            boxes[swap_i].animate.set_color(GREY_B),
            boxes[swap_j].animate.set_color(GREY_B),
            run_time=0.3,
        )
        counter = Text("Swaps: 1", font_size=26, color=TEAL_C).to_edge(DOWN, buff=0.6)
        self.play(FadeIn(counter), run_time=0.4)
        self.wait(4.0 - 0.4 - 0.6 - 0.3 - 0.4)  # 1.9s wait ✓

        # CUE 3 — 2.0s: Final FadeOut
        self.play(
            *[FadeOut(m) for m in self.mobjects],
            run_time=0.8,
        )
        self.wait(1.2)  # 0.8 + 1.2 = 2.0 ✓
