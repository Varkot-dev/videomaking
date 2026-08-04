from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        eq = Tex(r"x^2 + 1", font="Arial", font_size=48)
        self.play(Write(eq))
        self.wait(1.0)
