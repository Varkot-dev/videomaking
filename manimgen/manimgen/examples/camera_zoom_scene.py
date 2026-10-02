from manimlib import *


class CameraZoomScene(Scene):
    """
    techniques: camera_zoom
    Pattern: Camera zoom into a region of interest, then zoom back out.

    Key techniques demonstrated:
    - self.frame.animate.scale(factor).move_to(point)  to zoom in
    - self.frame.animate.scale(1/factor).move_to(ORIGIN)  to zoom back out
    - Always zoom OUT before the final FadeOut — mobjects outside the frame
      won't be captured by FadeOut if the camera is zoomed in elsewhere.
    - FlashAround() to highlight the focal object before zooming.

    Use this pattern when narration says "exactly", "precisely", "zoom in",
    "look closely", or "notice that".
    """

    def construct(self):
        title = Text("Zooming into a discontinuity", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)

        axes = Axes(
            x_range=[-1, 5, 1],
            y_range=[-1, 5, 1],
            width=8,
            height=4.5,
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
            y_axis_config={"include_numbers": False},
        ).center().shift(DOWN * 0.8)

        # y = x + 1 with a hole at x=2
        curve_left = axes.get_graph(lambda x: x + 1, color=BLUE, x_range=[-0.8, 2.0])
        curve_right = axes.get_graph(lambda x: x + 1, color=BLUE, x_range=[2.0, 4.8])
        hole = Circle(radius=0.1, color=RED, stroke_width=3, fill_opacity=0).move_to(axes.c2p(2, 3))

        self.play(ShowCreation(axes), run_time=1.0)
        self.play(ShowCreation(curve_left), ShowCreation(curve_right), run_time=1.5)
        self.play(ShowCreation(hole), run_time=0.5)
        self.wait(0.5)

        # Highlight the hole before zooming
        self.play(FlashAround(hole, color=YELLOW, run_time=0.8))

        # Zoom in: scale down the frame, center it on the hole
        zoom_target = axes.c2p(2, 3)
        self.play(
            self.frame.animate.scale(0.4).move_to(zoom_target),
            run_time=1.5,
        )
        self.wait(1.5)

        # Annotations visible only when zoomed in — small enough to fit
        annotation = VGroup(
            Text("f(2) undefined", font_size=18, color=RED),
            Text("limit = 3", font_size=18, color=GREEN),
        ).arrange(DOWN, buff=0.2)
        annotation.next_to(hole, RIGHT, buff=0.15)
        self.play(FadeIn(annotation), run_time=0.6)
        self.wait(1.0)

        # Zoom back out before cleanup — ALWAYS do this before FadeOut
        self.play(
            self.frame.animate.scale(1 / 0.4).move_to(ORIGIN),
            run_time=1.2,
        )
        self.wait(0.5)

        self.play(
            *[FadeOut(m) for m in self.mobjects],
            run_time=0.8,
        )
