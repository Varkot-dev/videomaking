from manimlib import *


class Section01Scene(ThreeDScene):
    def construct(self):
        axes = ThreeDAxes()
        self.add(axes)
        self.begin_ambient_camera_rotation(rate=0.2)
        self.wait(3.0)
