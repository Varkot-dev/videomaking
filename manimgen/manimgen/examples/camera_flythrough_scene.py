from manimlib import *
import numpy as np


class CameraFlythroughScene(ThreeDScene):
    """
    techniques: camera_flythrough
    Pattern: ThreeDScene camera flies through a sequence of viewpoints around a 3D surface.

    Key techniques demonstrated:
    - self.frame.reorient(theta_deg, phi_deg) to set exact camera angle
    - Animated camera moves: self.play(self.frame.animate.reorient(theta, phi), run_time=2)
    - Label pinned with label.fix_in_frame() so it stays on screen during orbit
    - Progressive surface + mesh build-up before flythrough starts

    Use when narration involves "look at this from another angle", "rotating around",
    "flying through", "see the structure from different perspectives", "3D geometry".
    """

    def construct(self):
        # Title — pinned to screen frame
        title = Text("A Surface in Three Dimensions", font_size=40, color=WHITE)
        title.fix_in_frame()
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=1.0)

        # Axes
        axes = ThreeDAxes(
            x_range=[-3, 3, 1],
            y_range=[-3, 3, 1],
            z_range=[-2, 2, 1],
        )
        axes.add_axis_labels()

        # Surface: z = sin(x) * cos(y)
        surface = ParametricSurface(
            lambda u, v: np.array([u, v, np.sin(u) * np.cos(v)]),
            u_range=(-PI, PI),
            v_range=(-PI, PI),
            resolution=(28, 28),
        )
        surface.set_color_by_xyz_func("z", min_value=-1.0, max_value=1.0, colormap="viridis")
        surface.set_shading(0.8, 0.5, 0.3)

        mesh = SurfaceMesh(surface, resolution=(10, 10))
        mesh.set_stroke(WHITE, 0.6, opacity=0.3)
        surface.add(mesh)

        # Start from a neutral top-down view
        self.frame.reorient(0, 90)
        self.play(ShowCreation(axes), run_time=1.2)
        self.play(ShowCreation(surface), run_time=2.0)

        # Flythrough: sequence of reorient() calls
        # Angle 1 — front-left elevated view
        self.play(
            self.frame.animate.reorient(-45, 70),
            run_time=2.0,
        )
        self.wait(0.5)

        # Angle 2 — low angle from the right
        self.play(
            self.frame.animate.reorient(60, 40),
            run_time=2.5,
        )

        label = Text("Peaks and valleys", font_size=28, color=YELLOW)
        label.fix_in_frame()
        label.to_corner(DR, buff=0.5)
        self.play(FadeIn(label), run_time=0.6)
        self.wait(0.8)

        # Angle 3 — overhead again, spinning back
        self.play(
            self.frame.animate.reorient(-30, 75),
            FadeOut(label),
            run_time=2.0,
        )
        self.wait(0.5)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
