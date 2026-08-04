from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        boxes = VGroup(*[Square(side_length=0.6) for _ in range(5)]).arrange(RIGHT)
        self.play(ShowCreation(boxes))
        for box in boxes:
            self.play(box.animate.set_color(YELLOW), run_time=0.4)
        self.wait(2.0)
