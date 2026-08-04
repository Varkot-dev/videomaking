from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 5], y_range=[0, 5], width=10, height=6)
        axes.set_color(STRUCT)
        label = Text("y = x^2", font_size=28, color=PRIMARY)
        label.next_to(axes, UP, buff=0.3)
        self.play(ShowCreation(axes))
        self.play(FadeIn(label))
        self.wait(1.0)
