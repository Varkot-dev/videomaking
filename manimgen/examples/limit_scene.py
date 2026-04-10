from manimlib import *


class LimitScene(Scene):
    """
    techniques: axes_curve, camera_zoom
    Pattern: Limits — canonical visual structure for any limit concept.

    A limit scene MUST contain ALL of the following:
    1. A function curve (not just a dashed horizontal line).
    2. A removable discontinuity (open dot) at the limit point.
    3. A moving dot approaching the limit point from both sides.
    4. Dashed guide lines from x-value and y-value to the open dot.
    5. The limit annotation grouped in a VGroup, placed outside the axes.

    Key ManimGL layout lessons:
    - y-axis labels rotate with the axis when include_numbers=True — add them manually instead.
    - set_width() controls the x-width; keep it at 7 so the scene fits with labels beside it.
    - With a title: axes.center().shift(DOWN * 0.5 + LEFT * 1.0) to leave room for right-side labels.
    - Always use whole-number ranges with step=1 for clean tick spacing.
    """

    def construct(self):
        # --- Title ---
        title = Text("The Limit: The Expected Value", font_size=48).to_edge(UP, buff=0.5)
        self.play(Write(title), run_time=1.0)
        self.wait(0.5)

        # --- Axes ---
        # x-axis uses include_numbers (horizontal, no rotation issue)
        # y-axis: do NOT use include_numbers — labels rotate with the axis. Add manually.
        axes = Axes(
            x_range=[0, 4, 1],
            y_range=[0, 4, 1],
            axis_config={"color": GREY_B, "include_tip": True},
            x_axis_config={
                "include_numbers": True,
                "decimal_number_config": {"font_size": 24},
            },
            y_axis_config={
                "include_numbers": False,  # add manually below to avoid rotation bug
            },
        ).set_width(6).center().shift(DOWN * 1.2 + LEFT * 0.5)

        # Add y-axis tick labels manually — positioned correctly, never rotated
        y_labels = VGroup()
        for n in [1, 2, 3]:
            lbl = Text(str(n), font_size=22, color=GREY_A)
            lbl.next_to(axes.y_axis.n2p(n), LEFT, buff=0.15)
            y_labels.add(lbl)

        # Axis name labels
        x_name = Text("x", font_size=26).next_to(axes.x_axis, RIGHT, buff=0.15)
        y_name = Text("f(x)", font_size=26).next_to(axes.y_axis, UP, buff=0.15)

        self.play(ShowCreation(axes), FadeIn(y_labels), Write(x_name), Write(y_name))
        self.wait(0.3)

        # --- Function curve: f(x) = x + 1, hole at x=1 ---
        curve_left = axes.get_graph(lambda x: x + 1, color=BLUE, x_range=[0.05, 0.93])
        curve_right = axes.get_graph(lambda x: x + 1, color=BLUE, x_range=[1.07, 2.8])

        self.play(ShowCreation(curve_left), ShowCreation(curve_right), run_time=1.5)
        self.wait(0.4)

        # --- Open dot at (1, 2) — removable discontinuity ---
        hole = Circle(radius=0.1, stroke_color=WHITE, fill_color="#1C1C1C", fill_opacity=1.0)
        hole.move_to(axes.c2p(1, 2))
        self.play(ShowCreation(hole))
        self.wait(0.3)

        # --- Dashed guide lines from axes to the hole ---
        h_dash = DashedLine(axes.c2p(0, 2), axes.c2p(1, 2), dash_length=0.12, color=BLUE_B, stroke_width=2)
        v_dash = DashedLine(axes.c2p(1, 0), axes.c2p(1, 2), dash_length=0.12, color=BLUE_B, stroke_width=2)
        self.play(ShowCreation(h_dash), ShowCreation(v_dash), run_time=0.8)
        self.wait(0.3)

        # --- Approaching dot from the left ---
        t = ValueTracker(0.05)
        dot_l = always_redraw(
            lambda: Dot(axes.input_to_graph_point(t.get_value(), curve_left), color=YELLOW, radius=0.09)
        )
        self.play(FadeIn(dot_l))
        self.play(t.animate.set_value(0.88), run_time=2.0, rate_func=linear)
        self.wait(0.2)
        self.play(FadeOut(dot_l))

        # --- Approaching dot from the right ---
        t2 = ValueTracker(2.8)
        dot_r = always_redraw(
            lambda: Dot(axes.input_to_graph_point(t2.get_value(), curve_right), color=YELLOW, radius=0.09)
        )
        self.play(FadeIn(dot_r))
        self.play(t2.animate.set_value(1.12), run_time=2.0, rate_func=linear)
        self.wait(0.2)
        self.play(FadeOut(dot_r))

        # --- Annotation: VGroup, placed to the RIGHT of axes, never stacked independently ---
        annotation = VGroup(
            Tex(r"y = 2", font_size=36, color=WHITE),
            Tex(r"\lim_{x \to 1} f(x) = 2", font_size=30, color=YELLOW),
        ).arrange(DOWN, buff=0.5, aligned_edge=LEFT)
        # Place in the right portion of the frame, vertically centered on the y=2 level
        annotation.move_to(RIGHT * 3.5 + DOWN * 0.3)

        self.play(Write(annotation), run_time=1.2)
        self.wait(1.2)

        # --- Clean exit ---
        self.play(
            *[FadeOut(m) for m in self.mobjects],
            run_time=0.8,
        )
        self.wait(0.5)
