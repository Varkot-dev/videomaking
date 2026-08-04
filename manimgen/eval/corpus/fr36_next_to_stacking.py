from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        line = DashedLine(LEFT * 3, RIGHT * 3, color=GREY_B)
        self.play(ShowCreation(line))
        low = Text("low", font_size=22).next_to(line, DOWN)
        high = Text("high", font_size=22).next_to(line, DOWN)
        self.play(FadeIn(low), FadeIn(high))
        self.wait(1.0)
