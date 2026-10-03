from manimlib import *
import numpy as np


class CrossSectionScene(ThreeDScene):
    """
    techniques: cross_section_3d
    Pattern: A ParametricSurface sliced by a horizontal plane that moves upward via ValueTracker.

    Key techniques demonstrated:
    - ParametricSurface for the bowl: z = x² + y² (paraboloid)
    - A flat Rectangle plane that moves up the z-axis, driven by ValueTracker
    - always_redraw intersection circle at the cutting height
    - Label with height value pinned with fix_in_frame()

    Use when narration involves "cross-section", "slicing", "level curves",
    "what you get if you cut through", "contour lines", "the intersection at height".
    """

    def construct(self):
        title = Text("Cross-Sections of a Paraboloid", font_size=40, color=WHITE)
        title.fix_in_frame()
        title.to_edge(UP, buff=0.4)
        self.play(Write(title), run_time=0.8)

        self.frame.reorient(-50, 65)

        axes = ThreeDAxes(
            x_range=[-2.5, 2.5, 1],
            y_range=[-2.5, 2.5, 1],
            z_range=[0, 4, 1],
        )

        # Paraboloid: z = x² + y²
        surface = ParametricSurface(
            lambda u, v: np.array([u, v, u**2 + v**2]),
            u_range=(-2.0, 2.0),
            v_range=(-2.0, 2.0),
            resolution=(24, 24),
        )
        surface.set_color_by_xyz_func("z", min_value=0.0, max_value=4.0, colormap="plasma")
        surface.set_shading(0.6, 0.4, 0.3)
        surface.set_opacity(0.7)
        surface.deactivate_depth_test()

        mesh = SurfaceMesh(surface, resolution=(8, 8))
        mesh.set_stroke(WHITE, 0.5, opacity=0.25)
        surface.add(mesh)

        self.play(ShowCreation(axes), run_time=1.0)
        self.play(ShowCreation(surface), run_time=1.8)

        # ValueTracker for cutting height z = h
        h = ValueTracker(0.2)

        # Cutting plane — a semi-transparent rectangle at height h
        cutting_plane = always_redraw(lambda: Rectangle(
            width=4.5, height=4.5,
            fill_color=BLUE,
            fill_opacity=0.18,
            stroke_color=BLUE_B,
            stroke_width=1.5,
        ).rotate(PI / 2, axis=RIGHT).move_to(np.array([0, 0, h.get_value()])))

        # Intersection circle — radius = sqrt(h) at z = h, drawn as a parametric curve
        def make_intersection_circle():
            r = max(h.get_value(), 0.001) ** 0.5
            z = h.get_value()
            points = [np.array([r * np.cos(a), r * np.sin(a), z]) for a in np.linspace(0, TAU, 64)]
            circle = VMobject()
            circle.set_points_as_corners([*points, points[0]])
            circle.set_stroke(YELLOW, 3)
            return circle

        intersection = always_redraw(make_intersection_circle)

        height_label = always_redraw(lambda: Text(
            f"z = {h.get_value():.1f}",
            font_size=30, color=YELLOW,
        ).fix_in_frame().to_corner(DR, buff=0.6))

        self.add(cutting_plane, intersection, height_label)
        self.play(ShowCreation(cutting_plane), run_time=0.8)

        # Start orbit during the slice animation
        self.frame.add_updater(lambda m, dt: m.increment_theta(-0.06 * dt))

        self.play(h.animate.set_value(3.5), run_time=5.0, rate_func=linear)
        self.wait(1.0)

        self.frame.clear_updaters()
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
