from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        counter = Text("0", font_size=48)
        self.play(FadeIn(counter))
        counter.set_text("1")
        self.wait(1.0)
