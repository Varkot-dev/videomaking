from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        eq = MathTex(r"e^{i\pi} + 1 = 0", font_size=48)
        self.play(Write(eq))
        self.wait(1.0)
