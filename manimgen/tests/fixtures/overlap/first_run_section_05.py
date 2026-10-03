from manimlib import *


class Section05Scene(Scene):
    def construct(self):
        # Archetype F-style card sequence: code_reveal, fade_reveal, tracker_label, equation_morph

        # CUE 0 — 5.75s
        title = Text("The correct loop", font_size=44, color=WHITE).to_edge(UP, buff=0.8)
        code_strs = [
            "lo = 0; hi = n - 1",
            "while lo <= hi:",
            "    mid = lo + (hi - lo) // 2",
            "    if A[mid] == t: return mid",
            "    if A[mid] < t: lo = mid + 1",
            "    else: hi = mid - 1",
            "return -1",
        ]
        code = VGroup(*[
            Text(s, font="Courier New", font_size=28, color=WHITE) for s in code_strs
        ]).arrange(DOWN, aligned_edge=LEFT, buff=0.12).center().shift(UP * 0.1)
        note = Text("Even experts get this wrong.", font_size=36, color=GOLD)
        note.next_to(code, DOWN, buff=0.35)

        self.play(Write(title), run_time=0.8)
        self.play(LaggedStart(*[FadeIn(l, shift=RIGHT * 0.2) for l in code], lag_ratio=0.3), run_time=3.0)
        self.play(FadeIn(note, shift=UP * 0.2), run_time=0.6)
        self.wait(1.35)  # 0.8+3.0+0.6+1.35 = 5.75

        # CUE 1 — 5.75s
        big = Text("90%", font_size=48, color=GOLD).move_to(UP * 0.9)
        big.set_backstroke(width=8)
        sub = Text("of professional programmers failed in two hours", font_size=36, color=WHITE)
        sub.next_to(big, DOWN, buff=0.25)
        sub.set_backstroke(width=8)
        squares = VGroup(*[
            Square(side_length=0.4, stroke_color=GREY_B, stroke_width=2,
                   fill_color=(GREEN if i == 9 else RED), fill_opacity=0.9)
            for i in range(10)
        ]).arrange(RIGHT, buff=0.12)
        squares.next_to(sub, DOWN, buff=0.35)
        cite = Text("(Bentley, Programming Pearls)", font_size=22, color=GREY_A)
        cite.next_to(squares, DOWN, buff=0.3)

        self.play(
            code.animate.set_opacity(0.25),
            note.animate.set_opacity(0.25),
            FadeIn(big, scale=0.8),
            run_time=0.8,
        )
        self.play(FadeIn(sub, shift=UP * 0.2), run_time=0.6)
        self.play(LaggedStart(*[FadeIn(s) for s in squares], lag_ratio=0.1), run_time=1.2)
        self.play(FadeIn(cite), run_time=0.5)
        self.wait(2.65)  # 0.8+0.6+1.2+0.5+2.65 = 5.75

        # CUE 2 — 5.75s
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.6)

        box_a = Square(side_length=0.9, fill_color="#2a2a2a", fill_opacity=1, stroke_color=GREY_B, stroke_width=2.5)
        box_b = Square(side_length=0.9, fill_color="#2a2a2a", fill_opacity=1, stroke_color=GREY_B, stroke_width=2.5)
        boxes = VGroup(box_a, box_b).arrange(RIGHT, buff=0.2).center()
        num_a = Text("23", font_size=36, color=WHITE).move_to(box_a)
        num_b = Text("38", font_size=36, color=WHITE).move_to(box_b)
        lo_label = Text("lo", font_size=28, color=BLUE_C).next_to(box_a, DOWN, buff=0.25)
        hi_label = Text("hi", font_size=28, color=BLUE_C).next_to(box_b, DOWN, buff=0.25)
        mid_rect = SurroundingRectangle(box_a, color=TEAL_A, buff=0.08, stroke_width=3)
        bad = Text("lo = mid", font_size=36, color=RED).next_to(boxes, UP, buff=0.7)

        t = ValueTracker(0)
        counter = always_redraw(lambda: VGroup(
            Text("Iterations:", font_size=28, color=WHITE),
            DecimalNumber(t.get_value(), num_decimal_places=0, font_size=36, color=WHITE),
        ).arrange(RIGHT, buff=0.2).to_corner(DL, buff=0.6))

        self.play(
            FadeIn(boxes), FadeIn(num_a), FadeIn(num_b),
            FadeIn(lo_label), FadeIn(hi_label), ShowCreation(mid_rect),
            FadeIn(bad), FadeIn(counter),
            run_time=1.0,
        )
        self.play(t.animate.set_value(99), run_time=2.0, rate_func=linear)
        good = Text("lo = mid + 1", font_size=36, color=GREEN).move_to(bad)
        self.play(FadeTransform(bad, good), run_time=0.6)
        self.play(
            lo_label.animate.next_to(box_b, DOWN, buff=0.25).shift(LEFT * 0.4),
            hi_label.animate.shift(RIGHT * 0.4),
            run_time=0.6,
        )
        self.wait(0.95)  # 0.6+1.0+2.0+0.6+0.6+0.95 = 5.75

        # CUE 3 — 5.75s
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.4)

        eq1 = Tex(r"mid = \frac{lo + hi}{2}", font_size=48, color=WHITE).move_to(UP * 0.5)
        given = Text("lo = 1,500,000,000   hi = 2,000,000,000", font_size=28, color=WHITE)
        given.move_to(DOWN * 0.7)
        self.play(Write(eq1), FadeIn(given), run_time=0.7)

        eq2 = Tex(r"lo + hi = 3{,}500{,}000{,}000 > 2{,}147{,}483{,}647", font_size=36, color=WHITE).move_to(UP * 0.5)
        self.play(TransformMatchingTex(eq1, eq2), run_time=0.6)

        eq3 = Tex(r"mid = -794{,}967{,}296", font_size=48, color=RED).move_to(UP * 0.5)
        rect = SurroundingRectangle(eq3, color=RED, buff=0.2)
        invalid = Text("Invalid index!", font_size=36, color=RED).move_to(DOWN * 1.9)
        self.play(TransformMatchingTex(eq2, eq3), run_time=0.6)
        self.play(ShowCreation(rect), FadeIn(invalid), run_time=0.3)

        eq4 = Tex(r"mid = lo + \frac{hi - lo}{2}", font_size=48, color=GREEN).move_to(UP * 0.5)
        safe = Text("Safe.", font_size=36, color=GREEN).move_to(DOWN * 1.9)
        self.play(
            TransformMatchingTex(eq3, eq4),
            FadeOut(rect), FadeOut(invalid),
            run_time=0.6,
        )
        self.play(FadeIn(safe, shift=UP * 0.2), run_time=0.3)
        self.wait(1.45)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        # 0.4+0.7+0.6+0.6+0.3+0.6+0.3+1.45+0.8 = 5.75