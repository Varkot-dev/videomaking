from manimlib import *


class ParametricSurfaceScene(ThreeDScene):
    """
    techniques: 3d_surface, camera_rotation
    Pattern: 3D parametric surface with axes, mesh overlay, xyz-based coloring, and ambient camera rotation.

    Key rules demonstrated here:
    - ThreeDScene base class enables depth test and 3D camera; never use Scene for 3D content.
    - self.frame.reorient(theta_degrees, phi_degrees) sets initial camera orientation.
    - self.frame.add_ambient_rotation(speed) drives continuous spin — remove updaters before FadeOut.
    - ThreeDAxes(x_range, y_range, z_range) for 3D coordinate frame.
    - ParametricSurface(uv_func, u_range, v_range) — uv_func returns a 3D point directly (no axes.c2p needed).
    - SurfaceMesh on top of ParametricSurface for wireframe overlay.
    - surface.set_color_by_xyz_func(glsl_snippet) — takes a GLSL string (e.g. "z"), not a Python lambda.
    - surface.set_shading(diffuse, specular, ambient) for lighting depth cues.
    - clear_updaters() on self.frame before FadeOut to stop rotation.
    """

    def construct(self):
        # --- Title ---
        title = Text("Parametric Surfaces", font_size=52).to_edge(UP, buff=0.5)
        self.add_fixed_in_frame_mobjects(title)
        self.play(Write(title), run_time=1.0)
        self.wait(0.3)

        # --- Initial camera orientation (degrees) ---
        self.frame.reorient(-30, 70)

        # --- 3D Axes ---
        axes = ThreeDAxes(
            x_range=[-3, 3, 1],
            y_range=[-3, 3, 1],
            z_range=[-1.5, 1.5, 0.5],
        )
        self.play(ShowCreation(axes), run_time=1.5)
        self.wait(0.3)

        # --- Parametric surface: z = sin(x) * cos(y) ---
        # uv_func returns world-space 3D point directly
        surface = ParametricSurface(
            lambda u, v: np.array([u, v, np.sin(u) * np.cos(v)]),
            u_range=(-PI, PI),
            v_range=(-PI, PI),
            resolution=(32, 32),
        )
        surface.set_color(BLUE_E)
        surface.set_shading(0.7, 0.3, 0.4)
        surface.set_opacity(0.85)

        self.play(ShowCreation(surface), run_time=2.5)
        self.wait(0.5)

        # --- Color by z position (GLSL snippet, not Python lambda) ---
        surface.set_color_by_xyz_func("z", min_value=-1.0, max_value=1.0, colormap="viridis")
        self.wait(0.5)

        # --- Wireframe mesh overlay ---
        mesh = SurfaceMesh(surface, resolution=(12, 12))
        self.play(ShowCreation(mesh), run_time=1.5)
        self.wait(0.4)

        # --- Formula label pinned to frame (not 3D space) ---
        formula = Tex(r"z = \sin(x)\cos(y)", color=YELLOW).scale(0.9)
        formula.to_corner(DL, buff=0.5)
        self.add_fixed_in_frame_mobjects(formula)
        self.play(FadeIn(formula, shift=RIGHT * 0.3), run_time=0.8)
        self.wait(0.3)

        # --- Ambient camera rotation ---
        self.frame.add_ambient_rotation(speed=0.15)
        self.wait(4.0)

        # --- Stop rotation, clean exit ---
        self.frame.clear_updaters()
        self.play(
            *[FadeOut(m) for m in self.mobjects],
            run_time=0.8,
        )
