from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        circle = Circle(radius=2, color=BLUE)
        self.add(circle)
        marker = Dot(circle.get_point_at_angle(PI / 4), color=YELLOW)
        self.play(FadeIn(marker))
        self.wait(1.0)
