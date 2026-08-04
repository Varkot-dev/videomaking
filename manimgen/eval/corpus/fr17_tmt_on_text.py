from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        counter = Text("5", font_size=48)
        new_counter = Text("11", font_size=48)
        self.play(FadeIn(counter))
        self.play(TransformMatchingTex(counter, new_counter, run_time=0.5))
        self.wait(1.0)
