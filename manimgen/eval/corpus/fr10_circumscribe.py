from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        eq = Tex(r"x = 5", font_size=48)
        self.play(Write(eq))
        self.play(Circumscribe(eq))
        self.wait(1.0)
