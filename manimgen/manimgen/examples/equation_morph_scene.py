from manimlib import *


class EquationMorphScene(Scene):
    """
    techniques: equation_morph
    Demonstrates algebra steps and equation transformations using TransformMatchingTex.

    KEY RULES shown here:
    - TransformMatchingTex(eq1, eq2) morphs shared symbols smoothly — use for algebra steps
    - Tex() for all math — supports font_size=, color=, and all LaTeX packages in preamble
    - Never Transform(text_a, text_b) for text — crashes when glyph counts differ
    - For proofs/logic: Tex(r"\forall", r"\in", r"\mathbb{N}") all work (amsmath in preamble)
    - Store intermediate values in Python variables, never read back from mobjects
    - Group labels with VGroup(...).arrange() — never two next_to() on the same anchor
    """

    def construct(self):
        # CUE 0 — 3.0s: Show starting equation, parts staggered via LaggedStart
        title = Text("Solving a Quadratic", font_size=34, color=WHITE).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=0.6)

        # LaggedStart reveals equation terms one by one (stagger_reveal pattern)
        eq0_parts = [
            Tex(r"x^2", font_size=48),
            Tex(r"-\ 5x", font_size=48),
            Tex(r"+\ 6", font_size=48),
            Tex(r"=\ 0", font_size=48),
        ]
        eq0_group = VGroup(*eq0_parts).arrange(RIGHT, buff=0.15).center()
        self.play(LaggedStart(*[Write(p) for p in eq0_parts], lag_ratio=0.3), run_time=1.2)
        self.wait(3.0 - 0.6 - 1.2)  # 1.2s ✓

        # Collapse into single Tex for TransformMatchingTex morphing
        self.remove(*eq0_parts)
        eq0 = Tex(r"x^2 - 5x + 6 = 0", font_size=48).center()
        self.add(eq0)

        # CUE 1 — 4.0s: Factor the quadratic
        eq1 = Tex(r"(x - 2)(x - 3) = 0", font_size=48).center()
        label1 = Text("Factor", font_size=24, color=TEAL_C)
        label1.next_to(eq1, DOWN, buff=0.5)

        self.play(TransformMatchingTex(eq0, eq1), run_time=1.2)
        self.play(FadeIn(label1), run_time=0.4)
        self.wait(4.0 - 1.2 - 0.4)  # 2.4s ✓

        # CUE 2 — 4.0s: Split into two solutions
        eq2a = Tex(r"x - 2 = 0", font_size=44, color=YELLOW)
        eq2b = Tex(r"x - 3 = 0", font_size=44, color=BLUE)
        solutions = VGroup(eq2a, eq2b).arrange(RIGHT, buff=1.2).center()

        self.play(FadeOut(label1), run_time=0.3)
        self.play(ReplacementTransform(eq1, solutions), run_time=1.0)
        self.wait(4.0 - 0.3 - 1.0)  # 2.7s ✓

        # CUE 3 — 4.0s: Show final answers
        ans_a = Tex(r"x = 2", font_size=48, color=YELLOW)
        ans_b = Tex(r"x = 3", font_size=48, color=BLUE)
        answers = VGroup(ans_a, ans_b).arrange(RIGHT, buff=1.6).center()

        self.play(
            TransformMatchingTex(eq2a, ans_a),
            TransformMatchingTex(eq2b, ans_b),
            run_time=1.0,
        )

        # Brace spanning both answers to label the solution set
        brace = Brace(answers, direction=DOWN, buff=0.15, color=WHITE)
        brace_label = Tex(r"\{2,\ 3\}", font_size=36, color=WHITE)
        brace_label.next_to(brace, DOWN, buff=0.2)

        self.play(
            GrowFromCenter(brace),
            FadeIn(brace_label),
            run_time=0.7,
        )
        self.wait(4.0 - 1.0 - 0.7)  # 2.3s ✓

        # CUE 4 — 2.0s: FadeOut
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(1.2)
