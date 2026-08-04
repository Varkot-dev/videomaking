from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        sq = Square(side_length=2)
        sq.set_fill_color(RED)
        self.play(ShowCreation(sq))
        self.wait(1.0)
