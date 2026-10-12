from manimlib import *
import numpy as np


class DotProduct3DScene(ThreeDScene):
    """
    techniques: dot_product_3d
    Pattern: Two 3D vectors with an angle arc and projection — geometric dot product visualization.

    Key techniques demonstrated:
    - 3D vectors drawn as Line from origin to tip with Arrow-style tip (Line + Sphere tip)
    - Angle label pinned with fix_in_frame()
    - Dashed projection line onto one vector
    - Camera orbits continuously: self.frame.add_updater(lambda m, dt: m.increment_theta(-0.1 * dt))

    Use when narration involves "dot product", "angle between vectors", "projection",
    "how similar are these vectors", "cosine similarity".
    """

    def construct(self):
        title = Text("The Dot Product — Geometrically", font_size=40, color=WHITE)
        title.fix_in_frame()
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        # Camera setup
        self.frame.reorient(-40, 70)

        # Two vectors in 3D
        v1_end = np.array([2.0, 0.5, 1.0])
        v2_end = np.array([0.5, 2.0, 0.5])

        v1 = Line(ORIGIN, v1_end, color=BLUE, stroke_width=4)
        v2 = Line(ORIGIN, v2_end, color=RED, stroke_width=4)

        tip1 = Sphere(radius=0.08).move_to(v1_end).set_color(BLUE)
        tip2 = Sphere(radius=0.08).move_to(v2_end).set_color(RED)

        label_v1 = Text("v", font_size=28, color=BLUE)
        label_v1.fix_in_frame()
        label_v1.to_corner(UL, buff=0.6)

        label_v2 = Text("w", font_size=28, color=RED)
        label_v2.fix_in_frame()
        label_v2.next_to(label_v1, RIGHT, buff=0.5)

        self.play(
            ShowCreation(v1), ShowCreation(v2),
            GrowFromCenter(tip1), GrowFromCenter(tip2),
            run_time=1.5,
        )
        self.play(Write(label_v1), Write(label_v2), run_time=0.6)

        # Orbit camera while showing geometry
        self.frame.add_updater(lambda m, dt: m.increment_theta(-0.08 * dt))

        # Projection of v2 onto v1
        v1_unit = v1_end / np.linalg.norm(v1_end)
        proj_scalar = np.dot(v2_end, v1_unit)
        proj_point = proj_scalar * v1_unit

        proj_line = DashedLine(v2_end, proj_point, dash_length=0.12, color=YELLOW, stroke_width=2)
        proj_dot = Sphere(radius=0.1).move_to(proj_point).set_color(YELLOW)

        self.play(ShowCreation(proj_line), GrowFromCenter(proj_dot), run_time=1.2)

        dot_val = np.dot(v1_end, v2_end)
        result_label = Text(f"v · w = {dot_val:.1f}", font_size=32, color=YELLOW)
        result_label.fix_in_frame()
        result_label.to_corner(DR, buff=0.6)
        self.play(Write(result_label), run_time=0.8)

        self.wait(2.5)

        self.frame.clear_updaters()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
