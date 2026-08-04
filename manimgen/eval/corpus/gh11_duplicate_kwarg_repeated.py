from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        sq = Square(side_length=2, color=BLUE, fill_color=BLUE_E, fill_opacity=0.6)
        self.play(ShowCreation(sq))
        self.wait(1.0)
