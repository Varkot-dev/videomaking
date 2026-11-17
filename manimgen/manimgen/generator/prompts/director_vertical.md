
---

# VERTICAL SHORT MODE — these rules OVERRIDE every layout number above

This scene is rendered at 1080x1920 (9:16) for YouTube Shorts. The frame is **4.5 units wide and 8 units tall**: x runs from -2.25 to 2.25, y from -4 to 4. Every landscape coordinate, width and font size earlier in this prompt is wrong for this frame. Use the numbers in this section instead.

## The safe box

The app draws buttons over the right edge and a caption over the bottom third. Anything that must be read stays inside:

- **x from -2.0 to 1.4**
- **y from -1.2 to 2.8**

That box is 3.4 units wide and 4.0 units tall, and its centre is at **x = -0.3**. Centre content on x = -0.3, not on ORIGIN. Nothing important goes below y = -1.2 or right of x = 1.4. Never use `to_edge` or `to_corner`: the edges are covered.

## Three bands (all centred on x = -0.3)

| Band | y range | What goes there |
|---|---|---|
| hook | 2.0 to 2.7 | the question: one or two short lines of `Text` |
| visual | -0.1 to 1.7 | the ONE main object, at most 3.3 wide and 1.8 tall |
| key | -1.1 to -0.4 | one to four key words, or one equation stacked over two lines |

## Sizes that fit

- `Text` and `Tex` font sizes: hook **28**, labels **22**, key line **36**. Only 18, 20, 22, 28 and 36 are used in a short (other values are snapped to these); never 44 or 48.
- A line of `Text` at font_size 28 fits about **18 characters**. Longer text MUST be split into two `Text` objects in a `VGroup(...).arrange(DOWN, buff=0.15)`.
- After building any text or group, guard its width: `if obj.get_width() > 3.3: obj.set_width(3.3)`.
- `Axes(width=3.0, height=1.7)` at most, with 5 to 7 ticks per axis and tick label `font_size` 20.
- A row of boxes holds at most 5 boxes of side 0.55. More items go in a second row or a column.
- Stroke widths of 4 for main shapes so they read on a phone.

## Vertical rules

- The hook text is added with `self.add(...)` BEFORE the first `self.play`, so it is visible in the very first frame. No title card, no intro animation of a title.
- Equations are broken at `=` and stacked, never one long line.
- Comparisons are stacked top over bottom, never left and right.
- Labels go beside the thing they label, inside the box. Two labels never share one position.
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

        # CUE 0 — 3.0s: the hook is already on screen in the very first frame
        hook = VGroup(
            Text("Why is 3-4-5", font_size=28, color=WHITE),
            Text("a right triangle?", font_size=28, color=WHITE),
        ).arrange(DOWN, buff=0.15).move_to([CX, 2.35, 0])
        tri = Polygon(
            [0, 0, 0], [1.9, 0, 0], [0, 1.4, 0],
            color=GREY_A, stroke_width=4,
        ).move_to([CX, 0.9, 0])
        self.add(hook)
        self.play(ShowCreation(tri), run_time=1.0)
        self.wait(2.0)

        # CUE 1 — 3.0s: label the sides one at a time, each beside its own side
        a_lab = Text("3", font_size=22, color=TEAL_B).next_to(tri, LEFT, buff=0.15)
        b_lab = Text("4", font_size=22, color=TEAL_B).next_to(tri, DOWN, buff=0.12)
        c_lab = Text("5", font_size=22, color=GOLD).move_to(
            tri.get_center() + np.array([0.35, 0.3, 0])
        )
        self.play(
            LaggedStart(FadeIn(a_lab), FadeIn(b_lab), FadeIn(c_lab), lag_ratio=0.4),
            run_time=1.5,
        )
        self.wait(1.5)

        # CUE 2 — 3.5s: the key equation, stacked in the key band
        line1 = Text("9 + 16", font_size=36, color=TEAL_B)
        line2 = Text("= 25", font_size=36, color=GOLD)
        VGroup(line1, line2).arrange(DOWN, buff=0.12).move_to([CX, -0.78, 0])
        self.play(Write(line1), run_time=0.8)
        self.wait(0.7)  # a beat of stillness before the reveal
        self.play(Write(line2), run_time=0.8)
        self.play(FlashAround(line2, color=GOLD), run_time=0.7)
        self.wait(0.5)
```
