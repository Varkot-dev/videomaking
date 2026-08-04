from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        nl = NumberLine(x_range=[0, 10, 1], stroke_width=2, tick_size=0.1)
        self.play(ShowCreation(nl))
        self.wait(1.0)
