from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("Done", font_size=48)
        self.play(FadeIn(title, scale_factor=1.2))
        self.wait(1.0)
