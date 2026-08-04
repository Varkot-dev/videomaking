from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        eq = Tex(r"a^2 + b^2 = c^2", font_size=48)
        self.play(Write(eq))
        parts = eq.get_parts_by_tex_expression("a^2")
        self.play(parts.animate.set_color(YELLOW))
        self.wait(1.0)
