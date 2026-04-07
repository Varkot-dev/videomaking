from manimlib import *


class TextRevealScene(Scene):
    """
    Pattern: Pure text scene — title, bullet points revealed one at a time,
    key term highlighted. No axes. Good for definitions, motivation, recap.

    Key lessons:
    - VGroup.arrange(DOWN, aligned_edge=LEFT) for bullet lists
    - LaggedStart with lag_ratio for staggered reveal
    - FadeIn vs Write: FadeIn for whole objects, Write for text where the drawing effect matters
    - Indicate() for attention-drawing pulse on an object
    - Always leave padding: to_edge(UP, buff=0.8) for title, items don't go below y=-3
    """

    def construct(self):
        title = Text("Three Things to Remember", font_size=52, color=BLUE).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.4)

        bullets = VGroup(
            Text("1. A limit describes tendency, not the actual value", font_size=34, color=WHITE),
            Text("2. Left and right limits must agree", font_size=34, color=WHITE),
            Text("3. The function need not be defined at that point", font_size=34, color=YELLOW),
        ).arrange(DOWN, buff=0.6, aligned_edge=LEFT)
        bullets.center().shift(DOWN * 0.5)

        # Reveal one by one
        for item in bullets:
            self.play(FadeIn(item, shift=RIGHT * 0.3), run_time=0.6)
            self.wait(0.5)

        self.wait(0.5)

        # Pulse-highlight the key point
        self.play(Indicate(bullets[2], color=YELLOW, scale_factor=1.05))
        self.wait(0.5)

        # Bring in a summary equation below
        eq = Tex(
            r"\lim_{x \to a} f(x) = L \quad \text{regardless of } f(a)",
            font_size=40, color=TEAL
        ).to_edge(DOWN, buff=1.0)
        self.play(Write(eq), run_time=1.5)
        self.wait(1.5)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.5)
