from manimlib import *


class Section01Scene(Scene):
    def construct(self):
        dot = Dot(color=YELLOW)
        self.add(dot)
        self.play(self.camera.frame.animate.set_width(8))
        self.wait(1.0)
