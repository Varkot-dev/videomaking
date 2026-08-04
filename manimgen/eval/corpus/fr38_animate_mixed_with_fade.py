from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        a = Text("A", font_size=36).shift(LEFT * 2)
        b = Text("B", font_size=36).shift(RIGHT * 2)
        self.add(a)
        self.play(a.animate.shift(UP), FadeIn(b))
        self.wait(1.0)
