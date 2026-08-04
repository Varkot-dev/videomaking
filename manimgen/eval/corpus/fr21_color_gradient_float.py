from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        n = 10
        cols = color_gradient([BLUE, RED], n / 2)
        group = VGroup(*[Square(side_length=0.5, color=c) for c in cols]).arrange(RIGHT)
        self.play(ShowCreation(group))
        self.wait(1.0)
