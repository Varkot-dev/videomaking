"""Hand-written ShortScene scenes that reproduce failures measured in real Shorts.

Each constant is the full source of one scene file (class name in the name of
the constant). Every scene ends by printing ``KIT-AUDIT`` followed by a tuple of
the kit's own audit and findings, after ``finish()`` has run, so the render test can
check the end state. They are plain data: nothing here is imported by the kit.
"""

# A 3-4-5 triangle with a square on each short side and the side lengths written on
# the sides. Failures measured: labels over the shapes, labels too small after
# the figure was scaled.
TRIANGLE = """from manimlib import *
from manimgen.scenekit import *


class TriangleScene(ShortScene):
    def construct(self):
        tri = Polygon([0, 0, 0], [4, 0, 0], [0, 3, 0], color=GREY_A, stroke_width=4)
        sq_a = Square(side_length=3).next_to(tri, LEFT, buff=0).align_to(tri, DOWN)
        sq_b = Square(side_length=4).next_to(tri, DOWN, buff=0).align_to(tri, LEFT)
        for sq, col in ((sq_a, TEAL_B), (sq_b, GREEN_B)):
            sq.set_stroke(col, 4).set_fill(col, opacity=0.25)
        self.diagram(tri, sq_a, sq_b, pad=0)

        self.hook("Why is 3-4-5", "a right triangle?")
        self.play(ShowCreation(tri), run_time=0.6)
        sides = [
            self.label("4", on=self.side(tri, 0)),
            self.label("5", on=self.side(tri, 1)),
            self.label("3", on=self.side(tri, 2)),
        ]
        self.play(*[FadeIn(s) for s in sides], run_time=0.4)
        squares = [
            (sq_a, self.label("9", on=sq_a)),
            (sq_b, self.label("16", on=sq_b)),
        ]
        for sq, lab in squares:
            self.play(FadeIn(sq), FadeIn(lab), run_time=0.4)
        key = self.key("9 + 16 = 25")
        self.play(Write(key), run_time=0.6)
        self.wait(0.4)
        self.finish()
        print("KIT-AUDIT", (self.audit(), self.findings))
"""

# A 4x4 grid with a brace under it and one beside it, both labelled "4".
# Failure measured: the label under the brace fell below the caption band.
GRID = """from manimlib import *
from manimgen.scenekit import *


class GridScene(ShortScene):
    def construct(self):
        cells = VGroup(*[Square(side_length=1) for _ in range(16)])
        cells.arrange_in_grid(4, 4, buff=0)
        cells.set_stroke(BLUE_B, 4).set_fill(BLUE_B, opacity=0.2)
        brace_b = Brace(cells, DOWN, buff=0.1)
        brace_r = Brace(cells, RIGHT, buff=0.1)
        self.diagram(cells, brace_b, brace_r)

        self.hook("How many squares", "in a 4 by 4 grid?")
        self.play(ShowCreation(cells), run_time=0.6)
        self.play(GrowFromCenter(brace_b), GrowFromCenter(brace_r), run_time=0.4)
        under = self.label("4", on=brace_b)
        beside = self.label("4", on=brace_r)
        self.play(FadeIn(under), FadeIn(beside), run_time=0.4)
        key = self.key("4 x 4 = 16")
        self.play(Write(key), run_time=0.6)
        self.wait(0.4)
        self.finish()
        print("KIT-AUDIT", (self.audit(), self.findings))
"""

# A three-line equation replaced by its conclusion, and a hook swapped too.
# Failures measured: old and new text ghosting together; faded text left at
# the end (the dimmed note is left at partial opacity on purpose).
EQUATION = """from manimlib import *
from manimgen.scenekit import *


class EquationScene(ShortScene):
    def construct(self):
        self.hook("Is 0.999... really", "equal to 1?")
        steps = self.key("x = 0.999...", "10x = 9.999...", "9x = 9", tex=True)
        self.play(FadeIn(steps), run_time=0.5)
        self.wait(0.5)
        self.swap_hook("So x = 1")
        self.swap_key("0.999... = 1", tex=True)
        note = self.label("the gap is zero", on=Dot([-0.3, -0.6, 0]))
        self.play(FadeIn(note), run_time=0.3)
        self.play(note.animate.set_opacity(0.3), run_time=0.3)
        self.wait(0.3)
        self.finish()
        print("KIT-AUDIT", (self.audit(), self.findings))
"""

SCENES = {
    "TriangleScene": TRIANGLE,
    "GridScene": GRID,
    "EquationScene": EQUATION,
}
