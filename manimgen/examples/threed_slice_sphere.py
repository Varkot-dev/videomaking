from manimlib import *
import numpy as np


class SlicedSphereScene(ThreeDScene):
    def construct(self):
        self.frame.reorient(-30, 70)

        # --- Parametric surface: z = sin(x)*cos(y) ---
        surface = ParametricSurface(
            lambda u, v: np.array([u, v, np.sin(u) * np.cos(v)]),
            u_range=(-PI, PI),
            v_range=(-PI, PI),
            resolution=(32, 32),
        )
        surface.set_color(BLUE_E)
        surface.set_shading(0.7, 0.3, 0.5)
        surface.set_opacity(0.85)

        mesh = SurfaceMesh(surface, resolution=(12, 12))

        self.play(ShowCreation(surface), run_time=2.0)
        self.play(ShowCreation(mesh), run_time=1.0)
        self.wait(0.5)

        # --- Sphere sitting above the surface ---
        sphere = Sphere(radius=1.2)
        sphere.set_color(RED_B)
        sphere.set_shading(0.9, 0.6, 0.4)
        sphere.move_to(OUT * 2.5)   # float above the surface along z

        self.play(GrowFromCenter(sphere), run_time=1.5)
        self.wait(1.0)

        # --- Slice: make x > 0 half translucent ---
        # get_points() returns (N, 3) array in uv order
        points = sphere.get_points()
        opacity = np.where(points[:, 0] > 0, 0.12, 1.0)
        sphere.deactivate_depth_test()
        sphere.set_opacity(opacity)
        self.wait(0.5)

        # --- Rotate camera so we can see inside ---
        self.frame.add_ambient_rotation(angular_speed=0.3)
        self.wait(4.0)

        # --- Label pinned to frame ---
        label = Text("Cross-section view", font_size=42, color=WHITE)
        label.to_edge(UP, buff=0.4)
        label.fix_in_frame()
        self.play(FadeIn(label), run_time=0.8)
        self.wait(2.0)

        # --- Clean exit ---
        self.frame.clear_updaters()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.0)
