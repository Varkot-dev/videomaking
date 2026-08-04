from manimlib import *


class Section01Scene(ThreeDScene):
    def construct(self):
        axes = ThreeDAxes()
        label = Text("3D", font_size=36)
        self.add(axes)
        self.add_fixed_in_frame_mobjects(label)
        self.wait(2.0)
