from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 5], y_range=[0, 5], x_length=10, y_length=6)
        self.play(ShowCreation(axes))
        self.wait(1.0)
