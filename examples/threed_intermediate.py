from manimlib import *


class ThreeDIntermediate(ThreeDScene):
    def construct(self):
        self.frame.reorient(-20, 75)

        # --- Act 1: Torus zooms in ---
        torus = Torus(r1=2.0, r2=0.5)
        torus.set_color(BLUE_E)
        torus.set_shading(0.8, 0.5, 0.5)

        self.play(GrowFromCenter(torus), run_time=2.0)
        self.frame.add_ambient_rotation(angular_speed=0.2)
        self.wait(1.5)

        # --- Act 2: Torus goes translucent, sphere slides in ---
        sphere = Sphere(radius=0.6)
        sphere.set_color(RED)
        sphere.set_shading(0.9, 0.6, 0.4)
        sphere.move_to(LEFT * 4)

        torus.deactivate_depth_test()
        self.play(
            torus.animate.set_opacity(0.2),
            FadeIn(sphere, shift=RIGHT * 4),
            run_time=2.0,
        )
        self.wait(2.0)

        # --- Act 3: Sphere slides out, cylinder rises in ---
        cylinder = Cylinder(height=2.5, radius=0.8)
        cylinder.set_color(TEAL)
        cylinder.set_shading(0.7, 0.4, 0.5)
        cylinder.move_to(DOWN * 4)

        label = Text("3D Animations", font_size=48, color=WHITE)
        label.to_edge(UP, buff=0.4)
        label.fix_in_frame()

        self.play(
            FadeOut(sphere, shift=RIGHT * 4),
            FadeIn(cylinder, shift=UP * 4),
            run_time=2.0,
        )
        self.play(FadeIn(label), run_time=1.0)
        self.wait(2.0)

        # --- Clean exit ---
        self.frame.clear_updaters()
        self.play(
            *[FadeOut(m) for m in self.mobjects],
            run_time=1.0,
        )
