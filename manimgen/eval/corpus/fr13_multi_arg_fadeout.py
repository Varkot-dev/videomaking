from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        a = Text("A", font_size=36).shift(LEFT * 2)
        b = Text("B", font_size=36).shift(RIGHT * 2)
        self.play(FadeIn(a), FadeIn(b))
        self.wait(1.0)
        self.play(FadeOut(a, b))
        self.wait(1.0)
