from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        arrow = Arrow(ORIGIN, ORIGIN, color=RED)
        self.play(ShowCreation(arrow))
        self.wait(1.0)
