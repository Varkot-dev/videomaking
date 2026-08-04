from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        boxes = VGroup(*[Square(side_length=0.8) for _ in range(4)]).arrange(RIGHT)
        scan_rect = SurroundingRectangle(boxes[0], color=YELLOW)
        self.play(ShowCreation(boxes), ShowCreation(scan_rect))
        self.play(scan_rect.become(SurroundingRectangle(boxes[2], color=YELLOW)), run_time=0.2)
        self.wait(1.0)
