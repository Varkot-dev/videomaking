from manimlib import *


class TextScene(Scene):
    """
    Pattern: Text display — title, subtitle, body text, fade in/out.
    Covers: Text, VGroup, Write, FadeIn, FadeOut, positioning.
    """

    def construct(self):
        # --- Title ---
        title = Text("Binary Search", font_size=64, color=BLUE)
        title.to_edge(UP, buff=0.8)

        # --- Subtitle ---
        subtitle = Text("Finding things fast in sorted lists", font_size=30, color=GREY_A)
        subtitle.next_to(title, DOWN, buff=0.4)

        # Write title first, then fade in subtitle
        self.play(Write(title))
        self.play(FadeIn(subtitle))
        self.wait(2)

        # --- Body bullets ---
        bullets = VGroup(
            Text("• Given: a sorted array", font_size=28),
            Text("• Goal: find a target value", font_size=28),
            Text("• Key insight: divide and conquer", font_size=28),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.35)
        bullets.move_to(ORIGIN)

        # Fade out header, bring in bullets with a lag
        self.play(FadeOut(title), FadeOut(subtitle))
        self.wait(0.3)
        self.play(LaggedStart(*[FadeIn(b) for b in bullets], lag_ratio=0.4))
        self.wait(2.5)

        # --- Highlight one bullet ---
        highlight = SurroundingRectangle(bullets[2], color=YELLOW, buff=0.1)
        self.play(ShowCreation(highlight))
        self.wait(1.5)

        # --- Clean exit ---
        self.play(FadeOut(bullets), FadeOut(highlight))
        self.wait(0.5)
