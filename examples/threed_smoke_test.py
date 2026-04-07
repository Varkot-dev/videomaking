from manimlib import *


class ThreeDSmokeTest(ThreeDScene):
    def construct(self):
        self.frame.reorient(-30, 70)

        axes = ThreeDAxes(x_range=[-3, 3, 1], y_range=[-3, 3, 1], z_range=[-2, 2, 1])

        sphere = Sphere(radius=1.0)
        sphere.set_color(BLUE)
        sphere.set_shading(0.7, 0.3, 0.4)

        self.play(ShowCreation(axes), run_time=1.0)
        self.play(ShowCreation(sphere), run_time=1.5)
        self.wait(1.0)
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
