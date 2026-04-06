# ManimGL Rules Core (shared by generator + retry)

## HARD RULES — WILL CRASH IF VIOLATED

- `from manimlib import *` only. NEVER `from manim import *`.
- `ShowCreation()` not `Create()`. `Tex()` not `MathTex()`. `self.frame` not `self.camera.frame`.
- `FlashAround()` not `Circumscribe()`.
- Output ONLY pure Python. No markdown fencing, no explanation.
- Background is dark (`#1C1C1C`), so keep contrast high (WHITE/BLUE/YELLOW/GREEN/RED).

## BANNED KWARGS — THESE DO NOT EXIST IN ManimGL

| Class | BANNED kwarg | Why / Alternative |
|---|---|---|
| `Arrow` | `tip_length` | Does not exist. Omit or use `max_tip_length_to_length_ratio`. |
| `Arrow` | `tip_width` | Does not exist. Omit or use `tip_width_ratio`. |
| `Arrow` | `tip_shape` | Does not exist. Omit entirely. |
| `Arrow` | `stroke_width` for styling | Arrow is a filled polygon. Use `thickness` or `fill_color`. |
| `SurroundingRectangle` | `corner_radius` | Does not exist. Use plain `SurroundingRectangle`. |
| `FadeIn` / `FadeOut` | `shift=` as a scalar | `shift` must be a 3D vector like `UP * 0.5`, never a number. |
| `FadeIn` / `FadeOut` | `scale_factor` | Does not exist in ManimGL. Remove entirely. |
| Any Mobject | `font` on Tex/TexText subclasses | Use `font` only on `Text()`, never on `Tex()` or `TexText()`. |
| Any Mobject | `target_position` | Does not exist. Use `.move_to()` or `.next_to()`. |

## COLOR NAMES — THESE WILL NameError

| WRONG (crashes) | CORRECT (ManimGL) |
|---|---|
| `DARK_GREY` / `DARK_GRAY` | `GREY_D` |
| `DARK_BLUE` | `BLUE_D` |
| `DARK_GREEN` | `GREEN_D` |
| `DARK_RED` | `RED_D` |
| `LIGHT_GREY` / `LIGHT_GRAY` | `GREY_A` |
| `LIGHT_BLUE` | `BLUE_A` |
| `LIGHT_GREEN` | `GREEN_A` |

Use `_A` (lightest) through `_E` (darkest) suffixes: `BLUE_A`, `BLUE_B`, `BLUE_C`, `BLUE_D`, `BLUE_E`.

## ANIMATION RULES — WILL CRASH IF VIOLATED

1. NEVER use `.animate` and `FadeIn`/`FadeOut` on the SAME mobject in one `self.play()` call.
   ```python
   # WRONG — crashes with TypeError
   self.play(obj.animate.move_to(UP), FadeIn(obj))
   # CORRECT — separate calls
   self.play(FadeIn(obj))
   self.play(obj.animate.move_to(UP))
   ```

2. NEVER create a zero-length Arrow (start == end). Causes divide-by-zero.
   ```python
   # WRONG
   Arrow(ORIGIN, ORIGIN)
   # CORRECT
   Arrow(ORIGIN, DOWN * 0.5)
   ```

3. NEVER pass a VGroup where a single Mobject is expected in FadeIn/FadeOut.

4. `DecimalNumber.set_value()` is animated via `.animate`:
   ```python
   self.play(counter.animate.set_value(100))
   ```

5. `always_redraw` lambdas must create a NEW mobject each call. Never mutate.

6. `color_gradient(colors, length)` — `length` MUST be int, never float. Use `int(n)`.

7. For plain numbers / non-LaTeX labels, use `Text(str(n))` not `Tex(str(n))` — avoids LaTeX dependency.
8. Never place labels directly on top of graphs/axes unless wrapped in `BackgroundRectangle`.

## DURATION RULES — MATCH THE TARGET

- The user prompt specifies a target duration in seconds.
- Your scene MUST last approximately that many seconds.
- Total duration = sum of all `self.play(... run_time=X)` durations + all `self.wait(Y)` durations.
- Default `run_time` for `self.play()` is 1 second. Default `self.wait()` is 1 second.
- Distribute `self.wait()` calls between animations to fill the target.
- Keep waits short (0.5 to 2.0), and keep final wait brief (<= 0.5).

## LAYOUT QUICK RULES

- Title goes at top: `to_edge(UP, buff=0.8)`.
- Main content goes in center-lower area: `.center().shift(DOWN * 0.5)` when title is visible.
- Axes should be explicitly sized and centered: `.set_width(10).center()`.
- Prefer one focal visual per beat, then clear before introducing the next beat.

## ARROW QUICK REFERENCE

```python
Arrow(start, end, buff=MED_SMALL_BUFF, thickness=3.0, tip_width_ratio=5, color=WHITE)
```
That's it. No `tip_length`, no `tip_width`, no `stroke_width`, no `scale_factor`.

## KEY API SIGNATURES (condensed)

```
Text(text, font_size=48, color=WHITE, font="", t2c={})
Tex(*tex_strings, font_size=48, color=WHITE, t2c={})
Circle(radius=1.0, stroke_color=RED, fill_color=None, fill_opacity=0.0)
Square(side_length=2.0)
Rectangle(width=4.0, height=2.0)
Arrow(start=LEFT, end=RIGHT, buff=MED_SMALL_BUFF, thickness=3.0, tip_width_ratio=5)
Line(start=LEFT, end=RIGHT, buff=0.0, path_arc=0.0)
Dot(point=ORIGIN, radius=DEFAULT_DOT_RADIUS, fill_color=WHITE)
SurroundingRectangle(mobject, buff=SMALL_BUFF, color=YELLOW)
Brace(mobject, direction=DOWN, buff=0.2)
Axes(x_range, y_range, axis_config={})
NumberPlane(x_range, y_range, background_line_style={})
VGroup(*vmobjects)  — .arrange(dir, buff)  .arrange_in_grid(n_rows, buff)
ValueTracker(value=0)
DecimalNumber(number=0, num_decimal_places=2, font_size=48)
```

## MANIMGL vs MANIMCOMMUNITY

| Wrong (ManimCE) | Correct (ManimGL) |
|---|---|
| `from manim import *` | `from manimlib import *` |
| `MathTex(r"x^2")` | `Tex(r"x^2")` |
| `Create(circle)` | `ShowCreation(circle)` |
| `Circumscribe(obj)` | `FlashAround(obj)` |
| `self.camera.frame` | `self.frame` |
| `Tex(r"\text{hello}")` | Works — or use `TexText("hello")` |
