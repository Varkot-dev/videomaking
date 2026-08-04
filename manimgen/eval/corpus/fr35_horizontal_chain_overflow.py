from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        title = Text("Derivation", font_size=48).to_edge(UP)
        eq1 = Tex(r"a = b", font_size=36).next_to(title, DOWN)
        eq2 = Tex(r"b = c", font_size=36).next_to(eq1, RIGHT)
        eq3 = Tex(r"c = d", font_size=36).next_to(eq2, RIGHT)
        self.play(Write(title), Write(eq1), Write(eq2), Write(eq3))
        self.wait(1.0)
