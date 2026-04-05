from manimlib import *


class ShapeScene(Scene):
    """
    Pattern: Shape animation — create, transform, recolor, group, fade.
    Covers: Circle, Square, Triangle, VGroup, ShowCreation, Transform,
            ReplacementTransform, set_color, arrange, GrowFromCenter.
    """

    def construct(self):
        # --- Create basic shapes ---
        circle = Circle(radius=1.2, color=BLUE)
        square = Square(side_length=2.4, color=RED)
        triangle = Triangle(color=GREEN).scale(1.4)

        # Arrange them side by side
        shapes = VGroup(circle, square, triangle).arrange(RIGHT, buff=1.0)
        shapes.move_to(ORIGIN)

        # Grow each shape from center with a stagger
        self.play(LaggedStart(
            GrowFromCenter(circle),
            GrowFromCenter(square),
            GrowFromCenter(triangle),
            lag_ratio=0.3,
        ))
        self.wait(1)

        # --- Recolor all shapes ---
        self.play(
            circle.animate.set_color(YELLOW),
            square.animate.set_color(PURPLE),
            triangle.animate.set_color(ORANGE),
        )
        self.wait(1)

        # --- Move them to a vertical stack ---
        self.play(shapes.animate.arrange(DOWN, buff=0.6).move_to(ORIGIN))
        self.wait(1)

        # --- Transform circle → square ---
        new_square = Square(side_length=2.4, color=BLUE).move_to(circle.get_center())
        self.play(ReplacementTransform(circle, new_square))
        self.wait(1)

        # --- Fade everything out ---
        self.play(FadeOut(shapes), FadeOut(new_square))
        self.wait(0.5)
