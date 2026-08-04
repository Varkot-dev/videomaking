from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("Dot Product", font_size=48).to_edge(UP)
        self.play(Write(title))
        self.wait(1.0)
        subtitle = Text("Geometric View", font_size=44).to_edge(UP)
        self.play(Write(subtitle))
        self.wait(1.0)
