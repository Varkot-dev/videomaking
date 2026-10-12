from manimlib import *


class JumpDiscontinuityScene(Scene):
    """
    techniques: split_screen, tracker_label
    Pattern: Jump discontinuity — two separate curve pieces, open circles at endpoints,
    dots approaching from each side, annotation showing limit DNE.

    Key lessons:
    - Use get_graph() with explicit x_range to draw each piece separately — no discontinuities= needed
    - Open circle = Circle(stroke_color=WHITE, fill_color='#1C1C1C') placed via axes.c2p()
    - Filled circle = Dot(axes.c2p(x, y)) for a defined value off the curve
    - Two approach dots need two separate ValueTrackers
    - Annotation grouped in VGroup placed to the RIGHT of axes, never overlapping
    """

    def construct(self):
        title = Text("When the Limit Doesn't Exist", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.3)

        axes = Axes(
            x_range=[-0.5, 3.5, 1],
            y_range=[-0.5, 4.5, 1],
            width=6,
            height=5,
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
            y_axis_config={"include_numbers": False},
        ).center().shift(DOWN * 0.8 + LEFT * 1.0)

        y_labels = VGroup(*[
            Text(str(n), font_size=22, color=GREY_A).next_to(axes.y_axis.n2p(n), LEFT, buff=0.15)
            for n in [1, 2, 3, 4]
        ])
        x_name = Text("x", font_size=26).next_to(axes.x_axis, RIGHT, buff=0.15)
        y_name = Text("f(x)", font_size=26).next_to(axes.y_axis, UP, buff=0.15)
        self.play(ShowCreation(axes), FadeIn(y_labels), Write(x_name), Write(y_name))
        self.wait(0.3)

        # Left piece: y = x + 1 for x < 2
        left_piece = axes.get_graph(lambda x: x + 1, color=YELLOW, x_range=[0.05, 1.93])
        # Right piece: y = x - 1 for x >= 2 (shifted down — visible jump)
        right_piece = axes.get_graph(lambda x: x - 1, color=YELLOW, x_range=[2.07, 3.45])
        self.play(ShowCreation(left_piece), ShowCreation(right_piece), run_time=2.0)
        self.wait(0.4)

        # Open circle at top of jump (left limit) and bottom of jump (right limit)
        open_top = Circle(radius=0.1, stroke_color=WHITE, fill_color="#1C1C1C", fill_opacity=1.0)
        open_top.move_to(axes.c2p(2, 3))  # left piece approaches y=3
        open_bot = Circle(radius=0.1, stroke_color=WHITE, fill_color="#1C1C1C", fill_opacity=1.0)
        open_bot.move_to(axes.c2p(2, 1))  # right piece approaches y=1
        self.play(ShowCreation(open_top), ShowCreation(open_bot))
        self.wait(0.3)

        # Vertical dashed line at x=2 to highlight the jump
        v_line = DashedLine(axes.c2p(2, 0), axes.c2p(2, 4), dash_length=0.12, color=GREY_B, stroke_width=2)
        self.play(ShowCreation(v_line))
        self.wait(0.2)

        # Approaching dot from the left
        tl = ValueTracker(0.3)
        dot_l = always_redraw(
            lambda: Dot(axes.input_to_graph_point(tl.get_value(), left_piece), color=RED, radius=0.09)
        )
        self.play(FadeIn(dot_l))
        self.play(tl.animate.set_value(1.88), run_time=2.5, rate_func=linear)
        self.wait(0.2)
        self.play(FadeOut(dot_l))

        # Approaching dot from the right
        tr = ValueTracker(3.4)
        dot_r = always_redraw(
            lambda: Dot(axes.input_to_graph_point(tr.get_value(), right_piece), color=BLUE, radius=0.09)
        )
        self.play(FadeIn(dot_r))
        self.play(tr.animate.set_value(2.12), run_time=2.5, rate_func=linear)
        self.wait(0.2)
        self.play(FadeOut(dot_r))

        # Annotation — grouped, placed RIGHT of axes
        ann = VGroup(
            Tex(r"\lim_{x\to 2^-} f(x) = 3", color=RED, font_size=28),
            Tex(r"\lim_{x\to 2^+} f(x) = 1", color=BLUE, font_size=28),
            Tex(r"\Rightarrow \text{Limit DNE}", color=WHITE, font_size=28),
        ).arrange(DOWN, buff=0.4, aligned_edge=LEFT)
        ann.next_to(axes, RIGHT, buff=0.4).shift(UP * 0.3)
        self.play(Write(ann), run_time=1.5)
        self.wait(1.5)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.5)
