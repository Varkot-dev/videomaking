from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        boxes = VGroup(*[Square(side_length=0.7) for _ in range(4)]).arrange(RIGHT)
        self.play(ShowCreation(boxes))
        i, j = 0, 2
        boxes[i], boxes[j] = boxes[j], boxes[i]
        self.wait(1.0)
