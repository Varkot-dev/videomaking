from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        axes = Axes(
            x_range=[0, 5], y_range=[0, 5], width=10, height=6,
            x_axis_config={"include_tip": False},
        )
        self.play(ShowCreation(axes))
        self.wait(1.0)
