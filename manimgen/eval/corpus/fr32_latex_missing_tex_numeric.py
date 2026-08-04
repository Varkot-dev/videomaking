from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        n = 42
        label = Tex(str(n), font_size=48)
        fixed = Tex("3.14", font_size=48)
        self.play(Write(label))
        self.play(Write(fixed))
        self.wait(1.0)
