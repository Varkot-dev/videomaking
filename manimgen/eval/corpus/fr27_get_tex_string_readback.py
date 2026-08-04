from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        labels = VGroup(*[Tex(str(v), font_size=36) for v in [5, 3, 8]]).arrange(RIGHT)
        self.play(ShowCreation(labels))
        a = int(labels[0].get_tex_string())
        b = int(labels[1].get_tex_string())
        if a > b:
            self.play(labels[0].animate.set_color(RED))
        self.wait(1.0)
