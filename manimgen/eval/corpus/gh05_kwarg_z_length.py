from manimlib import *


class Section01Scene(ThreeDScene):
    def construct(self):
        axes = ThreeDAxes(
            x_range=[-3, 3], y_range=[-3, 3], z_range=[-3, 3],
            x_length=6, y_length=6, z_length=4,
        )
        self.play(ShowCreation(axes))
        self.wait(1.0)
