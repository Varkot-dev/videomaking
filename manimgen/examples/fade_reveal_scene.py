from manimlib import *


class FadeRevealScene(Scene):
    """
    techniques: fade_reveal
    Demonstrates a dramatic reveal of a key insight after clearing clutter.

    KEY RULES shown here:
    - FadeOut existing elements before revealing the key insight (not all at once at end)
    - FadeIn(obj, shift=UP * 0.3) adds subtle motion to a reveal
    - Text over a busy background: obj.set_backstroke(width=8)
    - FlashAround(obj, color=YELLOW) to draw attention — never Circumscribe()
    - Short cue (< 2s): one animation + one wait only
    """

    def construct(self):
        # CUE 0 — 4.0s: Show a busy scene — multiple elements
        title = Text("Recursion", font_size=34, color=WHITE).to_edge(UP, buff=0.8)

        items = VGroup(*[
            Text(s, font_size=26, color=GREY_A)
            for s in [
                "f(n) calls f(n-1)",
                "f(n-1) calls f(n-2)",
                "f(n-2) calls f(n-3)",
                "... and so on ...",
            ]
        ]).arrange(DOWN, buff=0.4, aligned_edge=LEFT).center().shift(DOWN * 0.2)

        self.play(Write(title), run_time=0.5)
        self.play(
            LaggedStart(*[FadeIn(item) for item in items], lag_ratio=0.25),
            run_time=1.5,
        )
        self.wait(4.0 - 0.5 - 1.5)  # 2.0s ✓

        # CUE 1 — 1.5s: Short cue — highlight the pattern
        self.play(FlashAround(items[3], color=YELLOW), run_time=0.6)
        self.wait(0.9)  # 0.6 + 0.9 = 1.5 ✓

        # CUE 2 — 3.5s: Clear the clutter, reveal the key insight
        self.play(FadeOut(items), run_time=0.5)

        key_insight = Tex(r"f(n) = f(n-1) + f(n-2)", font_size=52, color=YELLOW)
        key_insight.center()
        key_insight.set_backstroke(width=6)

        self.play(FadeIn(key_insight, shift=UP * 0.25), run_time=0.9)

        # Surround the key insight to emphasize it
        highlight = SurroundingRectangle(key_insight, color=YELLOW, buff=0.2)
        self.play(ShowCreation(highlight), run_time=0.5)

        self.wait(3.5 - 0.5 - 0.9 - 0.5)  # 1.6s ✓

        # CUE 3 — 3.0s: Add a plain-text label below — Text() for non-math
        base_label = Text("Base cases: f(0) = 0, f(1) = 1", font_size=28, color=GREY_A)
        base_label.next_to(key_insight, DOWN, buff=0.7)

        self.play(FadeIn(base_label, shift=UP * 0.2), run_time=0.7)
        self.wait(3.0 - 0.7)  # 2.3s ✓

        # CUE 4 — 2.0s: FadeOut
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(1.2)
