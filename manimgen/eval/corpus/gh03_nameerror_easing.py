from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        dot = Dot(color=BLUE)
        self.add(dot)
        self.play(dot.animate.shift(RIGHT * 3), rate_func=ease_out_sine, run_time=2.0)
        self.wait(1.0)
