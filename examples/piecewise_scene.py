from manimlib import *


class PiecewiseScene(Scene):
    """
    Pattern: Piecewise function — multiple get_graph calls each with restricted x_range,
    open and filled endpoint dots, color-coded pieces.

    Key lessons:
    - Each piece is a separate axes.get_graph() with its own x_range — never one graph
    - Open endpoint: Circle(stroke_color=WHITE, fill_color='#1C1C1C', fill_opacity=1.0)
    - Filled endpoint: Dot(color=WHITE) placed via axes.c2p()
    - Brace can annotate each piece's domain
    - Color each piece differently so the viewer can track them
    """

    def construct(self):
        title = Text("A Piecewise Function", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)
        self.wait(0.3)

        axes = Axes(
            x_range=[-0.5, 5, 1],
            y_range=[-0.5, 5, 1],
            x_length=7,
            y_length=5,
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
            y_axis_config={"include_numbers": False},
        ).center().shift(DOWN * 0.8 + LEFT * 0.5)

        y_labels = VGroup(*[
            Text(str(n), font_size=22, color=GREY_A).next_to(axes.y_axis.n2p(n), LEFT, buff=0.15)
            for n in [1, 2, 3, 4]
        ])
        x_name = Text("x", font_size=26).next_to(axes.x_axis, RIGHT, buff=0.15)
        y_name = Text("f(x)", font_size=26).next_to(axes.y_axis, UP, buff=0.15)
        self.play(ShowCreation(axes), FadeIn(y_labels), Write(x_name), Write(y_name))
        self.wait(0.3)

        # Piece 1: f(x) = x^2 / 2 for x < 2 (blue)
        piece1 = axes.get_graph(lambda x: x**2 / 2, color=BLUE, x_range=[0.05, 1.93])
        # Open circle at x=2 for piece 1 (approaches y=2 from left)
        open1 = Circle(radius=0.1, stroke_color=BLUE, fill_color="#1C1C1C", fill_opacity=1.0)
        open1.move_to(axes.c2p(2, 2))

        # Piece 2: f(x) = 4 - x for 2 <= x <= 4 (teal)
        piece2 = axes.get_graph(lambda x: 4 - x, color=TEAL, x_range=[2.0, 3.95])
        # Filled dot at left endpoint x=2, y=2
        filled2_left = Dot(axes.c2p(2, 2), color=TEAL, radius=0.1)
        # Open circle at x=4, y=0
        open2_right = Circle(radius=0.1, stroke_color=TEAL, fill_color="#1C1C1C", fill_opacity=1.0)
        open2_right.move_to(axes.c2p(4, 0))

        # Piece 3: f(x) = 1 for x > 4 (yellow)
        piece3 = axes.get_graph(lambda x: 1, color=YELLOW, x_range=[4.05, 4.9])
        filled3 = Dot(axes.c2p(4, 1), color=YELLOW, radius=0.1)

        # Reveal piece by piece
        self.play(ShowCreation(piece1), ShowCreation(open1), run_time=1.2)
        self.wait(0.3)
        self.play(ShowCreation(piece2), FadeIn(filled2_left), ShowCreation(open2_right), run_time=1.2)
        self.wait(0.3)
        self.play(ShowCreation(piece3), FadeIn(filled3), run_time=0.8)
        self.wait(0.5)

        # Formula annotation
        formula = VGroup(
            Tex(r"f(x) = \begin{cases} x^2/2 & x < 2 \\ 4-x & 2 \le x < 4 \\ 1 & x > 4 \end{cases}",
                font_size=28, color=WHITE)
        ).next_to(axes, RIGHT, buff=0.5).shift(UP * 0.5)
        self.play(Write(formula), run_time=1.5)
        self.wait(1.5)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
        self.wait(0.5)
