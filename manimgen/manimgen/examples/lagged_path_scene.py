from manimlib import *


class LaggedPathScene(Scene):
    """
    techniques: lagged_path
    Pattern: Multiple objects arrive at final positions along arc paths with LaggedStart.

    Key techniques demonstrated:
    - ArcBetweenPoints(start, end, angle=PI/3) for curved flight paths
    - MoveAlongPath(dot, arc) to animate objects along the arc
    - LaggedStart(*[MoveAlongPath(...)], lag_ratio=0.15) for staggered wave-like arrival
    - GrowFromCenter for final "land" effect

    Use when narration involves "elements arriving", "coming together", "assembling",
    "converging from different directions", "particles", "nodes connecting".
    """

    def construct(self):
        title = Text("Elements Converging", font_size=44).to_edge(UP, buff=0.6)
        self.play(Write(title), run_time=0.8)

        # Final target positions — a 3×3 grid centered on screen
        target_positions = [
            LEFT * 2.5 + UP * 1.2,   UP * 1.2,    RIGHT * 2.5 + UP * 1.2,
            LEFT * 2.5,               ORIGIN,      RIGHT * 2.5,
            LEFT * 2.5 + DOWN * 1.2,  DOWN * 1.2,  RIGHT * 2.5 + DOWN * 1.2,
        ]

        colors = [BLUE, TEAL, GREEN, YELLOW, ORANGE, RED, PURPLE, MAROON, PINK]
        labels = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]

        # Start positions: off-screen at various edges
        start_positions = [
            LEFT * 7 + UP * 3,    UP * 4,           RIGHT * 7 + UP * 3,
            LEFT * 7,             DOWN * 5,          RIGHT * 7,
            LEFT * 7 + DOWN * 3,  DOWN * 4,          RIGHT * 7 + DOWN * 3,
        ]

        dots = VGroup(*[
            Circle(radius=0.35, fill_color=colors[i], fill_opacity=1, stroke_width=0)
            .move_to(start_positions[i])
            for i in range(9)
        ])
        dot_labels = VGroup(*[
            Text(labels[i], font_size=22, color=WHITE).move_to(start_positions[i])
            for i in range(9)
        ])

        # Build arc paths for each dot
        arcs = [
            ArcBetweenPoints(start_positions[i], target_positions[i], angle=PI / 3.5)
            for i in range(9)
        ]

        # Animate arrivals in a lagged wave
        self.play(
            LaggedStart(
                *[MoveAlongPath(dots[i], arcs[i]) for i in range(9)],
                lag_ratio=0.12,
            ),
            LaggedStart(
                *[MoveAlongPath(dot_labels[i], arcs[i]) for i in range(9)],
                lag_ratio=0.12,
            ),
            run_time=3.5,
        )
        self.wait(0.5)

        # Flash all at once to show they've arrived
        self.play(
            LaggedStart(
                *[FlashAround(dots[i], color=WHITE, time_width=0.5) for i in range(9)],
                lag_ratio=0.08,
            ),
            run_time=1.5,
        )
        self.wait(0.8)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
