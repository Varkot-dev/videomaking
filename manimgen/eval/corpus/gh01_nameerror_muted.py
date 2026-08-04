from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("Vector Fields", font_size=48).to_edge(UP)
        body = Text("A field assigns a vector to each point", font_size=28, color=MUTED)
        body.next_to(title, DOWN, buff=0.6)
        self.play(Write(title))
        self.play(FadeIn(body))
        self.wait(1.0)
