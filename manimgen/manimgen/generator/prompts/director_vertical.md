
---

# VERTICAL SHORT MODE — these rules OVERRIDE every layout number above

This scene is rendered at 1080x1920 (9:16) for YouTube Shorts. The frame is **4.5 units wide and 8 units tall**: x runs from -2.25 to 2.25, y from -4 to 4. Every landscape coordinate, width and font size earlier in this prompt is wrong for this frame. Use the numbers in this section instead.

## The safe box

The app draws buttons over the right edge and the title and channel name over the bottom of the frame. Everything stays inside:

- **x from -2.0 to 1.4**
- **y from -2.0 to 2.8**

That box is 3.4 units wide and 4.8 units tall, and its centre is at **x = -0.3**. Centre content on x = -0.3, not on ORIGIN. Nothing goes below y = -2.0 or right of x = 1.4. Never use `to_edge` or `to_corner`: the edges are covered.

## Three bands, top to bottom (all centred on x = -0.3)

| Band | y range | What goes there |
|---|---|---|
| hook | top edge at 2.75 | the question: one or two short lines of `Text` at font_size 28, placed with `hook.move_to([-0.3, 2.75, 0], aligned_edge=UP)` so its TOP sits at 2.75 whatever its height |
| key | top edge at 1.9 | ONE line: the key words or the equation, font_size 36, placed with `key.move_to([-0.3, 1.9, 0], aligned_edge=UP)`. Empty until the cue that needs it |
| diagram | -2.0 to 1.4 | the ONE main diagram, as big as it fits: up to 3.4 wide and 3.4 tall |

The diagram gets most of the screen. A phone screen is small; a diagram that is not big cannot be read.

## Sizes that fit

- `Text` and `Tex` font sizes: hook **28**, key line **36**, labels **28** (22 only for many small labels). Only 18, 20, 22, 28 and 36 are used in a short (other values are snapped to these); never 44 or 48.
- A line of `Text` at font_size 28 fits about **18 characters**, at 36 about **14**. Longer hook text MUST be split into two `Text` objects in a `VGroup(...).arrange(DOWN, buff=0.12)`. The key line is one line: shorten it rather than wrap it.
- After building any text or group, guard its width: `if obj.get_width() > 3.3: obj.set_width(3.3)`.
- **Build the diagram full size, then scale it to the band.** Draw it in convenient units (a 3-4-5 triangle as 3 and 4 units), put every part in one `VGroup`, then before the first animation: `main.scale(min(3.4 / main.get_width(), 3.4 / main.get_height()))` and `main.move_to([-0.3, -0.3, 0])`. Labels, braces and brace labels that go outside the shapes (under a grid, beside a side) are built up front and put in `main` BEFORE the scale, so they are inside the 3.4 box too; a label added after the scale below the diagram lands under the bottom edge. Parts added in later cues are positioned relative to parts already in `main` (`next_to`, `move_to(part)`), so they scale with it. Build every part up front (unanimated), scale once, then animate the parts in.
- `Axes(width=3.2, height=3.0)` at most, with 5 to 7 ticks per axis and tick label `font_size` 20.
- A row of boxes holds at most 5 boxes of side 0.6. More items go in a second row or a column.
- Stroke widths of 4 for main shapes so they read on a phone.
- **Labels must read.** A label on a shape is WHITE, font_size 28 (or 22 when there are many), centred in a fill of opacity 0.3 or less, with no `set_backstroke`. Never colour a label the same as the fill it sits on. A label for a line or a point goes just outside it with `next_to`.

## Vertical rules

- The hook text is added with `self.add(...)` BEFORE the first `self.play`, so it is visible in the very first frame. No title card, no intro animation of a title.
- Equations are broken at `=` and stacked, never one long line.
- Comparisons are stacked top over bottom, never left and right.
- Labels go on or beside the thing they label, inside the box. Two labels never share one position.
- Replacing text in a band (a new hook line, a new key line): the old text must be fully gone before the new text starts to appear. Never put `FadeOut(old)` and `FadeIn(new)` for the same position in one `self.play`; the two lines ghost over each other and neither can be read. Fade the old one out in its own short play, `self.play(FadeOut(old_hook), FadeOut(old_key), run_time=0.25)`, then `FadeIn` the new text, alone or together with the cue's other animations. Count the 0.25 s in the cue's time. This overrides the one-play `FadeOut(a), FadeIn(b)` pattern shown earlier.
- Keep objects on screen and transform them. One visual change per narrated statement.
- Before the key reveal, a short `self.wait` of 0.5 to 1.0 seconds of stillness; hold the frame after it.
- The last cue ends on the payoff frame. No `FadeOut` of everything at the end, no outro.
- `Scene` only. No `ThreeDScene`, no camera moves except a gentle `self.frame.animate.scale(...)` between 0.8 and 1.0.

## Verified vertical reference scene — copy this layout

```python
from manimlib import *


class ShortExampleScene(Scene):
    def construct(self):
        CX = -0.3

        # Build the whole diagram first, in convenient units, then scale it
        # once to fill the diagram band.
        tri = Polygon([0, 0, 0], [4, 0, 0], [0, 3, 0], color=GREY_A, stroke_width=4)
        sq_a = Square(side_length=3).next_to(tri, LEFT, buff=0).align_to(tri, DOWN)
        sq_b = Square(side_length=4).next_to(tri, DOWN, buff=0).align_to(tri, LEFT)
        for sq, col in ((sq_a, TEAL_B), (sq_b, GREEN_B)):
            sq.set_stroke(col, 4).set_fill(col, opacity=0.25)
        main = VGroup(tri, sq_a, sq_b)
        main.scale(min(3.4 / main.get_width(), 3.4 / main.get_height()))
        main.move_to([CX, -0.3, 0])

        # CUE 0 — 3.0s: the hook is already on screen in the very first frame
        hook = VGroup(
            Text("Why is 3-4-5", font_size=28, color=WHITE),
            Text("a right triangle?", font_size=28, color=WHITE),
        ).arrange(DOWN, buff=0.12).move_to([CX, 2.75, 0], aligned_edge=UP)
        self.add(hook)
        self.play(ShowCreation(tri), run_time=1.0)
        self.wait(2.0)

        # CUE 1 — 3.0s: a square on each short side, its count written inside
        lab_a = Text("9", font_size=28, color=WHITE).move_to(sq_a)
        lab_b = Text("16", font_size=28, color=WHITE).move_to(sq_b)
        self.play(FadeIn(sq_a), FadeIn(lab_a), run_time=0.8)
        self.play(FadeIn(sq_b), FadeIn(lab_b), run_time=0.8)
        self.wait(1.4)

        # CUE 2 — 3.5s: the key line appears in its band, under the hook
        key = Text("9 + 16 = 25", font_size=36, color=GOLD).move_to([CX, 1.9, 0], aligned_edge=UP)
        self.play(Write(key), run_time=1.0)
        self.wait(0.7)  # a beat of stillness before the reveal
        self.play(FlashAround(key, color=GOLD), run_time=0.8)
        self.wait(1.0)
```
