from manimlib import *


class Section04Scene(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 5], y_range=[0, 5], width=10, height=6)
        labels = axes.get_axis_labels(, y_label="y")
        self.play(ShowCreation(axes), FadeIn(labels))
        self.wait(1.0)
