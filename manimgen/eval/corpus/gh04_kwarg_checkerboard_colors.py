from manimlib import *


class Section01Scene(ThreeDScene):
    def construct(self):
        surface = ParametricSurface(
            lambda u, v: np.array([u, v, u * v]),
            u_range=[-2, 2],
            v_range=[-2, 2],
            checkerboard_colors=[BLUE_D, BLUE_E],
        )
        self.play(ShowCreation(surface))
        self.wait(1.0)
