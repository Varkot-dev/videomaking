from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        line = Line(LEFT * 3, RIGHT * 3, color=DARK_GREY)
        box = Square(side_length=1, color=LIGHT_GREY)
        self.play(ShowCreation(line), ShowCreation(box))
        self.wait(1.0)
