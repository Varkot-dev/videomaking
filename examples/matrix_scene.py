from manimlib import *


class MatrixScene(Scene):
    """
    Pattern: Matrix transformation — show a number plane grid being
    transformed by a 2x2 matrix, with the matrix displayed on screen.
    Covers: NumberPlane, ApplyMatrix, IntegerMatrix, VGroup, Write, Transform.
    """

    def construct(self):
        # --- Title ---
        title = Text("Linear Transformation", font_size=48, color=BLUE)
        title.to_edge(UP, buff=0.5)
        self.play(Write(title))
        self.wait(0.5)

        # --- Draw the number plane ---
        plane = NumberPlane(
            x_range=[-4, 4, 1],
            y_range=[-3, 3, 1],
            background_line_style={"stroke_color": BLUE_E, "stroke_opacity": 0.5},
        )
        self.play(ShowCreation(plane), run_time=1.5)
        self.wait(0.5)

        # --- Show the matrix ---
        matrix_data = [[1, 2], [0, 1]]  # shear matrix
        matrix_mob = IntegerMatrix(matrix_data)
        matrix_mob.scale(0.8)
        matrix_mob.to_corner(UL, buff=0.8)
        matrix_mob.shift(DOWN * 0.5)

        matrix_label = Text("M = ", font_size=32)
        matrix_label.next_to(matrix_mob, LEFT, buff=0.1)

        self.play(Write(matrix_label), Write(matrix_mob))
        self.wait(1)

        # --- Apply the transformation to the plane ---
        self.play(
            plane.animate.apply_matrix(matrix_data),
            run_time=2,
            rate_func=smooth,
        )
        self.wait(1.5)

        # --- Show what happened to a specific vector ---
        origin = plane.get_origin()
        v_start = plane.coords_to_point(1, 1)
        arrow = Arrow(origin, v_start, color=RED, buff=0)
        self.play(GrowArrow(arrow))
        self.wait(1)

        # --- Clean exit ---
        self.play(FadeOut(plane), FadeOut(matrix_mob), FadeOut(matrix_label),
                  FadeOut(arrow), FadeOut(title))
        self.wait(0.5)
