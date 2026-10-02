from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        cells = VGroup(*[Square(side_length=0.6) for _ in range(6)])
        cells.arrange_in_grid(n_rows=2, n_cols=3, row_buff=0.5, col_buff=0.3)
        self.play(ShowCreation(cells))
        self.wait(1.0)
