---

# VERTICAL SHORT MODE: write the scene with the layout kit

This scene is rendered at 1080x1920 (9:16) for YouTube Shorts. Every landscape coordinate, width and font size earlier in this prompt is wrong for this frame, and so is any layout rule above that conflicts with this section.

You do NOT place text. A base class, `ShortScene`, owns every position: you say what to show, it decides where. Text placed with raw coordinates is the main way these scenes went wrong (labels drawn over shapes, text too small after scaling a diagram, titles under the app's buttons, two lines ghosting during a swap). The kit makes those mistakes hard to make.

Always start the file with exactly these two imports and inherit `ShortScene`:

```
from manimlib import *
from manimgen.scenekit import *


class Section01Scene(ShortScene):
    def construct(self):
        ...
```

Nothing else from `manimgen` may be imported. `Scene` is not used; no `ThreeDScene`; no camera moves except a gentle `self.frame.animate.scale(...)` between 0.8 and 1.0.

## The kit API

| Call | What it does | Time it costs |
|---|---|---|
| `self.hook("line", ...)` | the question at the top, white. Wraps long lines itself. Already ON SCREEN in the first frame; do not animate it in. Returns it | 0 |
| `self.key("text")` | the key words or equation, gold, under the hook. Not on screen yet: `self.play(Write(key))`. Several strings stack as lines (an equation derivation). `tex=True` makes LaTeX. Returns it | 0 |
| `self.swap_hook("new", ...)` | replaces the hook: old text fully out (0.25 s), then the new text in. Returns the new hook | 0.25 + `run_time` (default 0.5) |
| `self.swap_key("new", ...)` | same for the key line. Returns the new key | 0.25 + `run_time` (default 0.5) |
| `self.diagram(*shapes, pad=0.4)` | groups the shapes, scales them to fit the diagram area and centres them. Shapes only, never Text or Tex. `pad` is the room kept free inside the area for labels outside the shapes (a number, or `(left, bottom, right, top)`); use `pad=0` when every label goes inside a shape. Returns the group, nothing is added to the scene | 0 |
| `self.label("text", on=target)` | a label for a shape, a brace, a `Dot`, or a side of a polygon (`on=self.side(poly, i)`, side i runs from vertex i to vertex i+1). It picks the spot: inside a closed shape with a faint fill if it fits, else away from the diagram, else right, left, above, below; it never covers other text, crosses a line or sits on another filled shape. `prefer="inside"/"outside"/"right"/"left"/"above"/"below"` is a hint only. `tex=True` for math. Returns the label, not on screen yet: `self.play(FadeIn(lab))` | 0 |
| `self.drop(a, b)` | fades things out in a play of their own and forgets them (so their space is free again) | `run_time` (default 0.25) |

Rules that follow from the table:

- All on-screen text comes from `hook`, `key` and `label`. Never `Text(...)`, `Tex(...)`, `move_to`, `to_edge`, `next_to` or `font_size=` for text. The kit chooses the size (never below 22).
- Build all shapes first, call `self.diagram(...)` ONCE, then animate the shapes in. Shapes added to the diagram later must be built from parts already in it (`next_to`, `move_to(part)`) before the `diagram` call, or be added without a label.
- Create each label AFTER `self.diagram(...)` (the kit needs the final shape positions). Create all labels for a cue just before they appear. A label's text is fixed; to change a number, `self.drop(old_label)` and make a new label.
- A `Brace` goes INTO `self.diagram(...)` with the shapes it spans, then `self.label("4", on=brace)`.
- Axes: no tick numbers (`include_numbers=False`); label the few ticks that matter with `self.label(..., on=Dot(...))` or skip them. `Axes(width=3, height=3)` at most.
- Stroke widths of 4 for the main shapes. Fill opacity 0.3 or less on a shape that carries a label inside it; otherwise the label goes outside.
- Equations are broken at `=` and stacked (`self.key("x = 0.999...", "10x = 9.999...", tex=True)`), never one long line. Comparisons are stacked top over bottom, never side by side.
- A stacked key takes the space of the diagram: do not show a diagram and a 3-line key together.
- Replace the hook or key only with `swap_hook` / `swap_key`. Never `FadeOut(old)` and `FadeIn(new)` for the same text in one `self.play`.
- Keep objects on screen and transform them. One visual change per narrated statement.
- Before the key reveal, a `self.wait` of 0.5 to 1.0 seconds of stillness; hold the frame after it.
- The last cue ends on the payoff frame. No `FadeOut` of everything at the end, no outro. (Anything left half-faded is faded out for you at the end.)
- Every second counts as before: `swap_hook`, `swap_key` and `drop` have the times in the table, and the cue's total must match the cue length.

## Verified vertical reference scene: copy this structure

```python
from manimlib import *
from manimgen.scenekit import *


class ShortExampleScene(ShortScene):
    def construct(self):
        # Shapes only, built in convenient units. The kit scales and centres
        # them; both labels go inside squares, so no room is kept for outside labels.
        tri = Polygon([0, 0, 0], [4, 0, 0], [0, 3, 0], color=GREY_A, stroke_width=4)
        sq_a = Square(side_length=3).next_to(tri, LEFT, buff=0).align_to(tri, DOWN)
        sq_b = Square(side_length=4).next_to(tri, DOWN, buff=0).align_to(tri, LEFT)
        for sq, col in ((sq_a, TEAL_B), (sq_b, GREEN_B)):
            sq.set_stroke(col, 4).set_fill(col, opacity=0.25)
        self.diagram(tri, sq_a, sq_b, pad=0)

        # CUE 0 — 3.0s: the hook is already on screen in the very first frame
        self.hook("Why is 3-4-5", "a right triangle?")
        self.play(ShowCreation(tri), run_time=1.0)
        self.wait(2.0)

        # CUE 1 — 3.0s: a square on each short side, its count written inside
        lab_a = self.label("9", on=sq_a)
        lab_b = self.label("16", on=sq_b)
        self.play(FadeIn(sq_a), FadeIn(lab_a), run_time=0.8)
        self.play(FadeIn(sq_b), FadeIn(lab_b), run_time=0.8)
        self.wait(1.4)

        # CUE 2 — 4.0s: the key line, first as a question, then the answer
        key = self.key("9 + 16 = ?")
        self.play(Write(key), run_time=1.0)
        self.wait(0.5)  # a beat of stillness before the reveal
        key = self.swap_key("9 + 16 = 25")  # costs 0.25 + 0.5 = 0.75
        self.play(FlashAround(key, color=GOLD), run_time=0.8)
        self.wait(0.95)
```
