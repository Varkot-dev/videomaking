# ManimGL Rules Core (shared by generator + retry)

## HARD RULES — WILL CRASH IF VIOLATED

- `from manimlib import *` only. NEVER `from manim import *`.
- `ShowCreation()` not `Create()`. `Tex()` not `MathTex()`. `self.frame` not `self.camera.frame`.
- Output ONLY pure Python. No markdown fencing, no explanation.

## BANNED KWARGS — THESE DO NOT EXIST IN ManimGL

| Class | BANNED kwarg | Why / Alternative |
|---|---|---|
| `Arrow` | `tip_length` | Does not exist. Omit or use `max_tip_length_to_length_ratio`. |
| `Arrow` | `tip_width` | Does not exist. Omit or use `tip_width_ratio`. |
| `Arrow` | `tip_shape` | Does not exist. Omit entirely. |
| `Arrow` | `stroke_width` for styling | Arrow is a filled polygon. Use `thickness` or `fill_color`. |
| `SurroundingRectangle` | `corner_radius` | Does not exist. Use plain `SurroundingRectangle`. |
| `FadeIn` / `FadeOut` | `shift=` as a scalar | `shift` must be a 3D vector like `UP * 0.5`, never a number. |
| Any Mobject | `font` on Tex/Tex subclasses | Use `font` only on `Text()`, never on `Tex()`. |

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

3. NEVER pass a VGroup where a single Mobject is expected in FadeIn/FadeOut scale_factor.

4. `DecimalNumber.set_value()` is animated via `.animate`:
   ```python
   self.play(counter.animate.set_value(100))
   ```

5. `always_redraw` lambdas must create a NEW mobject each call. Never mutate.

## ARROW QUICK REFERENCE

```python
Arrow(start, end, buff=MED_SMALL_BUFF, thickness=3.0, tip_width_ratio=5, color=WHITE)
```
That's it. No `tip_length`, no `tip_width`, no `stroke_width`.

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
