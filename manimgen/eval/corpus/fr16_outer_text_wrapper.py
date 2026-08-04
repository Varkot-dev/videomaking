from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        label = Tex(r"\text{Total Sum}", font_size=44)
        self.play(Write(label))
        self.wait(1.0)
