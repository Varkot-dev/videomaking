from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        left_values = [5, 3, 8, 1, 9, 2, 7, 4, 6, 0]
        right_values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 0]
        left_group = VGroup(*[Square(side_length=0.7) for v in left_values]).arrange(RIGHT).shift(LEFT * 2.8)
        right_group = VGroup(*[Square(side_length=0.7) for v in right_values]).arrange(RIGHT).shift(RIGHT * 2.8)
        self.play(ShowCreation(left_group), ShowCreation(right_group))
        self.wait(1.0)
