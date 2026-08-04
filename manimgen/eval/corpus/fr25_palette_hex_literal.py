from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        dot = Dot(color="#00D9FF")
        ring = Circle(radius=1.2, color="#FF6B35")
        self.play(FadeIn(dot), ShowCreation(ring))
        self.wait(1.0)
