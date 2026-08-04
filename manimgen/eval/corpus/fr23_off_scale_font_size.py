from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("Overview", font_size=53).to_edge(UP)
        body = Text("Supporting detail", font_size=31)
        body.next_to(title, DOWN, buff=0.6)
        self.play(Write(title), FadeIn(body))
        self.wait(1.0)
