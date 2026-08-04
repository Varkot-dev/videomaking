from manimlib import *


class Section02Scene(ThreeDScene):
    def construct(self):
        surface = ParametricSurface(
            lambda u, v: np.array([u, v, u * v]),
            u_range=[-2, 2],
            resolution=20, BLUE_E],
        )
        self.play(ShowCreation(surface))
        self.wait(1.0)
