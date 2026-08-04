from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        axes = Axes(x_range=[0, 5], y_range=[0, 25], width=10, height=6)
        graph = axes.get_graph(lambda x: x ** 2, color=TEAL_A)
        p = axes.get_graph_point(2, graph)
        self.play(ShowCreation(axes), ShowCreation(graph))
        self.play(FadeIn(Dot(p)))
        self.wait(1.0)
