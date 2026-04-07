from manimlib import *
import numpy as np


class InteriorRevealScene(ThreeDScene):
    def construct(self):
        self.frame.reorient(-20, 75)

        # --- Inner torus ---
        torus = Torus(r1=0.9, r2=0.3)
        torus.set_color(YELLOW)
        torus.set_shading(0.9, 0.6, 0.4)
        torus_mesh = SurfaceMesh(torus, resolution=(12, 8))
        torus_mesh.set_stroke(YELLOW_E, 1, opacity=0.6)
        torus.add(torus_mesh)

        # --- Outer sphere with mesh for structure ---
        sphere = Sphere(radius=1.8)
        sphere.set_color(TEAL_E)
        sphere.set_shading(0.8, 0.5, 0.5)
        sphere_mesh = SurfaceMesh(sphere, resolution=(18, 12))
        sphere_mesh.set_stroke(TEAL_A, 0.8, opacity=0.4)
        sphere.add(sphere_mesh)

        # Torus hidden inside — add silently, sphere conceals it
        self.add(torus)
        self.play(FadeIn(sphere), run_time=2.0)
        self.wait(1.0)

        # --- Camera orbits (frame moves, objects stay fixed — both visible together) ---
        self.frame.add_updater(lambda m, dt: m.increment_theta(-0.15 * dt))
        self.wait(1.5)

        # --- Reveal: uniform glass transparency, depth test off so torus shows through ---
        sphere.deactivate_depth_test()
        self.play(sphere.animate.set_opacity(0.08), run_time=1.5)
        self.wait(5.0)

        # --- Label pinned to frame ---
        label = Text("Interior structure", font_size=42, color=WHITE)
        label.to_edge(UP, buff=0.4)
        label.fix_in_frame()
        self.play(FadeIn(label), run_time=0.8)
        self.wait(2.0)

        # --- Clean exit ---
        self.frame.clear_updaters()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=1.0)
