from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        circle = Circle(radius=1.5, color=TEAL_A)
        self.play(Create(circle))
        self.wait(1.0)
