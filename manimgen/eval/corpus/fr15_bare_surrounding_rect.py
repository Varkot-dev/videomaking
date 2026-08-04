from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        label = Text("Result", font_size=36)
        self.play(FadeIn(label))
        self.play(SurroundingRectangle(label, color=YELLOW))
        self.wait(1.0)
