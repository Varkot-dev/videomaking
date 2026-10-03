from manimlib import *


class Section01Scene(Scene):
    # Trimmed from a real Director draft: `Star` is not exported by ManimGL 1.7.2,
    # so this scene crashed at render time with NameError.
    def construct(self):
        title = Text("Gradient Descent", font_size=48, color=WHITE).to_edge(
            UP, buff=0.8
        )
        self.play(Write(title), run_time=0.6)

        pts = [
            np.array([-6, 1.5, 0]),
            np.array([-2, -1.0, 0]),
            np.array([0, -1.8, 0]),
            np.array([6, 1.4, 0]),
        ]
        curve = VMobject(color=TEAL_A, stroke_width=5)
        curve.set_points_smoothly(pts)
        self.play(ShowCreation(curve), run_time=2.0)

        dot = Dot(pts[1], radius=0.15, color=WHITE)
        star = Star(n=5, outer_radius=0.25, color=GOLD)
        star.move_to(pts[2])
        low = Text("lowest point", font_size=28, color=GOLD).next_to(
            star, DOWN, buff=0.3
        )
        self.play(FadeIn(VGroup(dot, star, low)), run_time=1.0)
        self.wait(1.0)
