from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("Intro", font_size=48)
        self.play(Write(title), run_time=2.0)
        self.wait(0)
        self.play(FadeOut(title))
        self.wait(-0.5)
