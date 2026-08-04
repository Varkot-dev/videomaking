from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        arrow = Arrow(LEFT * 2, RIGHT * 2, tip_length=0.3, color=GOLD)
        self.play(ShowCreation(arrow))
        self.wait(1.0)
