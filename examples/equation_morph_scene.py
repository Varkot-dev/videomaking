from manimlib import *


class EquationMorphScene(Scene):
    """
    techniques: equation_morph
    Pattern: Algebra step-by-step — equations build up line by line, then
    morph into each other using TransformMatchingTex.

    Key techniques demonstrated:
    - TransformMatchingTex(eq_old, eq_new)  morphs matching subexpressions
    - LaggedStart for staggered line-by-line reveal of a derivation
    - SurroundingRectangle to box the key result
    - VGroup.arrange(DOWN) for stacking equation lines with consistent spacing

    Use this when narration involves algebra steps, simplification, or
    "watch this transform into".
    """

    def construct(self):
        title = Text("Factoring a difference of squares", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.5)

        # Starting expression
        eq0 = Tex(r"x^2 - 4", color=WHITE).scale(1.4).center().shift(UP * 0.5)
        self.play(Write(eq0), run_time=1.0)
        self.wait(0.8)

        # Morph into expanded form
        eq1 = Tex(r"x^2 - 2^2", color=YELLOW).scale(1.4).center().shift(UP * 0.5)
        self.play(TransformMatchingTex(eq0, eq1), run_time=1.2)
        self.wait(0.5)

        # Morph into factored form
        eq2 = Tex(r"(x - 2)(x + 2)", color=GREEN).scale(1.4).center().shift(UP * 0.5)
        self.play(TransformMatchingTex(eq1, eq2), run_time=1.5)
        self.wait(0.5)

        # Box the result
        box = SurroundingRectangle(eq2, color=YELLOW, buff=0.2)
        self.play(ShowCreation(box), run_time=0.6)
        self.wait(0.5)

        # Show the derivation as stacked lines below
        steps = VGroup(
            Tex(r"a^2 - b^2 = (a-b)(a+b)", color=GREY_A),
            Tex(r"x^2 - 2^2 = (x-2)(x+2)", color=GREY_A),
        ).arrange(DOWN, buff=0.5, aligned_edge=LEFT)
        steps.center().shift(DOWN * 1.8)

        self.play(
            LaggedStart(*[Write(line) for line in steps], lag_ratio=0.4),
            run_time=2.0,
        )
        self.wait(1.0)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
