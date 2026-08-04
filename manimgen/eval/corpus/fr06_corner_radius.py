from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        label = Text("Hello", font_size=36)
        box = SurroundingRectangle(label, corner_radius=0.2, color=YELLOW)
        self.play(FadeIn(label), ShowCreation(box))
        self.wait(1.0)
