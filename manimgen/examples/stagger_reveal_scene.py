from manimlib import *


class StaggerRevealScene(Scene):
    """
    techniques: stagger_reveal
    Demonstrates items appearing one by one with LaggedStart.

    KEY RULES shown here:
    - LaggedStart(*[FadeIn(item) for item in items], lag_ratio=0.15) is the standard pattern
    - Text() for plain labels (no LaTeX), Tex() for math
    - VGroup.arrange() for positioning — never two independent next_to() on same anchor
    - font_size= works on both Text() and Tex()
    """

    def construct(self):
        # CUE 0 — 3.5s: Title + reveal a list of items one by one
        title = Text("Binary Search Steps", font_size=36, color=WHITE).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=0.7)

        steps = [
            "1. Find the middle element",
            "2. Compare with target",
            "3. Discard the wrong half",
            "4. Repeat on remaining half",
        ]
        step_labels = VGroup(*[
            Text(s, font_size=28, color=GREY_A)
            for s in steps
        ]).arrange(DOWN, buff=0.45, aligned_edge=LEFT).center().shift(DOWN * 0.3)

        self.play(
            LaggedStart(*[FadeIn(l) for l in step_labels], lag_ratio=0.3),
            run_time=1.8,
        )
        self.wait(1.0)  # 0.7 + 1.8 + 1.0 = 3.5 ✓

        # CUE 1 — 4.0s: Highlight each step in sequence as narrator describes it
        for label in step_labels:
            self.play(label.animate.set_color(YELLOW), run_time=0.3)
            self.wait(0.55)
            self.play(label.animate.set_color(WHITE), run_time=0.2)
        # 4 steps × (0.3 + 0.55 + 0.2) = 4.2s — close enough, adjust last wait
        self.wait(4.0 - 4 * (0.3 + 0.55 + 0.2) + 0.2)

        # CUE 2 — 3.5s: Show a math expression revealing term by term
        eq_parts = [Tex(r"O(", font_size=40), Tex(r"\log", font_size=40, color=YELLOW),
                    Tex(r"n", font_size=40), Tex(r")", font_size=40)]
        eq = VGroup(*eq_parts).arrange(RIGHT, buff=0.05).center().shift(DOWN * 0.2)

        complexity_label = Text("Time complexity:", font_size=28, color=GREY_B)
        complexity_label.next_to(eq, UP, buff=0.4)

        self.play(FadeIn(complexity_label), run_time=0.4)
        self.play(
            LaggedStart(*[Write(p) for p in eq_parts], lag_ratio=0.4),
            run_time=1.6,
        )
        self.wait(3.5 - 0.4 - 1.6)  # 1.5s wait ✓

        # CUE 3 — 2.0s: FadeOut
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(1.2)
