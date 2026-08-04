from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        group = VGroup(*[Dot() for _ in range(3)]).arrange(RIGHT)
        self.add(group)
        for m in group._mobjects:
            self.play(m.animate.set_color(RED), run_time=0.2)
        self.wait(1.0)
