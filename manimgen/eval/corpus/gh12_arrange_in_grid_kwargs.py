from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        cells = VGroup(*[Square(side_length=0.6) for _ in range(12)])
        cells.arrange_in_grid(rows=3, cols=4, row_buff=0.2)
        self.play(ShowCreation(cells))
        self.wait(1.0)
