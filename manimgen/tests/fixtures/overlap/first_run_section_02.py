from manimlib import *


class Section02Scene(Scene):
    def construct(self):
        # Archetype F-ish custom: stagger_reveal + split_screen + fade_reveal
        shuffled = [41, 7, 93, 28, 65, 12, 80, 34, 57, 3, 71, 19, 88, 46, 24, 60]
        sorted_vals = sorted(shuffled)

        def make_row(vals):
            row = VGroup()
            for v in vals:
                sq = Square(side_length=0.55, fill_color="#2a2a2a", fill_opacity=1,
                            stroke_width=2, color=GREY_B)
                tx = Text(str(v), font_size=20, color=WHITE).move_to(sq)
                row.add(VGroup(sq, tx))
            row.arrange(RIGHT, buff=0.1).center()
            return row

        # CUE 0 — 6.33s
        title = Text("Looking for 60", font_size=44, color=WHITE).to_edge(UP, buff=0.8)
        top_row = make_row(shuffled)
        target = Text("Target: 60", font_size=28, color=GOLD)
        target.next_to(top_row, UP, buff=0.3).align_to(top_row, RIGHT)

        self.play(Write(title), run_time=0.8)
        self.play(LaggedStart(*[FadeIn(b) for b in top_row], lag_ratio=0.12), run_time=2.5)
        self.play(Write(target), run_time=0.6)
        self.wait(2.43)

        # CUE 1 — 6.33s
        divider = Line(LEFT * 6.5, RIGHT * 6.5, color=GREY_B, stroke_width=2)
        self.play(
            top_row.animate.shift(UP * 1.5),
            target.animate.shift(UP * 1.5),
            ShowCreation(divider),
            run_time=1.0,
        )
        top_rect = SurroundingRectangle(top_row[7], color=TEAL_A, buff=0.06, stroke_width=3)
        q_text = Text("Is 34 < 60? Yes. So what?", font_size=28, color=WHITE)
        q_text.next_to(top_row, DOWN, buff=0.3)
        q_text.set_x(top_row[7].get_center()[0])
        nothing = Text("Nothing learned about the other 15", font_size=28, color=GREY_A)
        nothing.next_to(q_text, DOWN, buff=0.25)
        self.play(ShowCreation(top_rect), Write(q_text), run_time=0.8)
        self.play(Write(nothing), run_time=0.7)

        bottom_row = make_row(sorted_vals).shift(DOWN * 1.5)
        self.play(LaggedStart(*[FadeIn(b) for b in bottom_row], lag_ratio=0.1), run_time=1.5)
        bot_rect = SurroundingRectangle(bottom_row[7], color=TEAL_A, buff=0.06, stroke_width=3)
        self.play(ShowCreation(bot_rect), run_time=0.5)
        self.wait(1.83)

        # CUE 2 — 6.33s
        ruled = VGroup(*bottom_row[:8])
        self.play(
            top_row.animate.set_opacity(0.25),
            target.animate.set_opacity(0.25),
            FadeOut(top_rect),
            FadeOut(q_text),
            FadeOut(nothing),
            FadeOut(bot_rect),
            ruled.animate.set_color(GREY_D).set_opacity(0.25),
            run_time=1.2,
        )
        brace = Brace(ruled, DOWN, buff=0.15, color=TEAL_A)
        ruled_label = Text("ruled out", font_size=28, color=WHITE).next_to(brace, DOWN, buff=0.15)
        win = bottom_row[sorted_vals.index(60)]
        self.play(
            GrowFromCenter(brace),
            Write(ruled_label),
            win[0].animate.set_fill(GREEN, opacity=0.35).set_stroke(color=GREEN, width=3),
            run_time=1.0,
        )
        gold_text = Text("One comparison. Eight cards gone.", font_size=44, color=GOLD)
        gold_text.move_to(UP * 1.5)
        gold_text.set_backstroke(width=8)
        self.play(FadeIn(gold_text, shift=UP * 0.2), run_time=1.0)
        self.wait(2.33)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)