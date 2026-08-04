from manimlib import *


class Section01Scene(ThreeDScene):
    def construct(self):
        axes = ThreeDAxes()
        self.add(axes)
        self.set_camera_orientation(phi=60 * DEGREES, theta=-45 * DEGREES)
        self.wait(2.0)
