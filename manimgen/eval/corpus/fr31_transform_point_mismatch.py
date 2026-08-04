from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        old = Text("5", font_size=48)
        new = Text("11", font_size=48)
        self.play(FadeIn(old))
        self.play(Transform(old, new))
        self.wait(1.0)
