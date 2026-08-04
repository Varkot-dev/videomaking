from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("The Algorithm: Pointers, Midpoint, and Comparison", font_size=36).to_edge(UP)
        self.play(Write(title))
        self.wait(1.0)
