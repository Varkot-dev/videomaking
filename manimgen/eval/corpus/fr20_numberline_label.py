from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        nl = NumberLine(x_range=[0, 10, 1], label="values", width=10)
        self.play(ShowCreation(nl))
        self.wait(1.0)
