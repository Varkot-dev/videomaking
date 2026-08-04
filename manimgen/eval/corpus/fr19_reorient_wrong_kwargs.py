from manimlib import *


class Section01Scene(ThreeDScene):
    def construct(self):
        axes = ThreeDAxes()
        self.add(axes)
        self.frame.reorient(theta_deg=-45, phi_deg=60)
        self.wait(2.0)
