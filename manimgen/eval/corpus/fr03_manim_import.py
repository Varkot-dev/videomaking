from manim import *


class Section01Scene(Scene):
    def construct(self):
        self.play(ShowCreation(Circle()))
        self.wait(1.0)
