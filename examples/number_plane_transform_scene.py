from manimlib import *


class NumberPlaneTransformScene(Scene):
    """
    Pattern: NumberPlane grid warping — linear matrix transform or complex map.

    Key techniques demonstrated:
    - NumberPlane() as a full-screen coordinate grid background
    - grid.animate.apply_matrix([[a,b],[c,d]])  for linear transforms
    - FadeTransform(label_old, label_new)  swap description while grid warps
    - set_backstroke(width=5) on text over busy backgrounds for readability

    Use when narration involves "linear transformation", "matrix", "shear",
    "rotation", or "what this matrix does geometrically".
    """

    def construct(self):
        title = Text("A shear matrix in action", font_size=48).to_edge(UP, buff=0.6)
        title.set_backstroke(width=8)
        self.play(Write(title), run_time=1.0)

        # Grid before transform
        grid = NumberPlane(
            x_range=[-6, 6, 1],
            y_range=[-4, 4, 1],
            background_line_style={"stroke_color": BLUE_E, "stroke_width": 1.5, "stroke_opacity": 0.6},
        )
        grid.add_coordinate_labels(font_size=20)

        label_before = VGroup(
            Text("Before:", font_size=32, color=GREY_A),
            Tex(r"\begin{pmatrix}1 & 1\\ 0 & 1\end{pmatrix}", color=WHITE).scale(0.9),
        ).arrange(RIGHT, buff=0.3)
        label_before.to_corner(UR, buff=0.5).shift(DOWN * 0.6)
        label_before.set_backstroke(width=6)

        self.play(ShowCreation(grid), run_time=1.5)
        self.play(Write(label_before), run_time=0.8)
        self.wait(0.5)

        # Apply shear matrix [[1,1],[0,1]]
        shear_matrix = [[1, 1], [0, 1]]
        label_after = VGroup(
            Text("Sheared:", font_size=32, color=YELLOW),
            Tex(r"\begin{pmatrix}1 & 1\\ 0 & 1\end{pmatrix}", color=YELLOW).scale(0.9),
        ).arrange(RIGHT, buff=0.3)
        label_after.to_corner(UR, buff=0.5).shift(DOWN * 0.6)
        label_after.set_backstroke(width=6)

        self.play(
            grid.animate.apply_matrix(shear_matrix),
            FadeTransform(label_before, label_after),
            run_time=3.0,
        )
        self.wait(1.5)

        # Show a vector that got transformed
        origin = grid.c2p(0, 0) if hasattr(grid, 'c2p') else ORIGIN
        vec = Arrow(ORIGIN, grid.c2p(1, 1), buff=0, color=RED, thickness=4.0)
        vec_label = Text("(1,1)→(2,1)", font_size=26, color=RED)
        vec_label.next_to(vec.get_end(), RIGHT, buff=0.15)
        vec_label.set_backstroke(width=6)

        self.play(GrowArrow(vec), run_time=0.8)
        self.play(Write(vec_label), run_time=0.5)
        self.wait(1.0)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
