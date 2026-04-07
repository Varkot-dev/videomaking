# ManimGen — Session A: Template Engine

## What you're building

The ManimGen pipeline currently works like this:
```
section dict → LLM → raw Python ManimGL code → manimgl render
```

This produces inconsistent, broken layouts because the LLM writes raw pixel-level
positioning code from scratch every time. Your job is to build the template layer
that replaces this.

New architecture:
```
section dict → LLM → visual spec JSON → template engine → guaranteed-correct ManimGL .py
```

You own the RIGHT SIDE: the spec schema + all template classes.

---

## Critical: ManimGL vs ManimCommunity

This project uses `manimgl` (3b1b's fork), NOT `manim` (ManimCommunity).

| Correct (ManimGL)         | WRONG (ManimCommunity)  |
|---------------------------|-------------------------|
| `from manimlib import *`  | `from manim import *`   |
| `ShowCreation(obj)`       | `Create(obj)`           |
| `Tex(...)`                | `MathTex(...)`          |
| `self.frame`              | `self.camera.frame`     |
| `FlashAround(obj)`        | `Circumscribe(obj)`     |

**BANNED kwargs** (crash at runtime): `tip_length`, `tip_width`, `tip_shape`,
`corner_radius`, `scale_factor`, `target_position`, `font=` on `Tex`/`TexText`.

**Color names that NameError:**
- `DARK_GREY` → `GREY_D`, `DARK_BLUE` → `BLUE_D`, `DARK_GREEN` → `GREEN_D`
- `DARK_RED` → `RED_D`, `LIGHT_GREY` → `GREY_A`, `LIGHT_GRAY` → `GREY_A`

`Tex()` does NOT accept `font_size=`. Only `Text()` does.
For axis tick font size, nest it: `axis_config={"decimal_number_config": {"font_size": 24}}`

Background is always dark charcoal `#1C1C1C` (enforced by runner.py `-c` flag).
Keep foreground colors high-contrast: WHITE, BLUE, YELLOW, GREEN, RED.

---

## Project layout (relevant parts)

```
manimgen/
├── manimgen/
│   ├── generator/
│   │   ├── scene_generator.py     ← Session B rewrites this (do not touch)
│   │   └── prompts/
│   ├── validator/
│   │   └── codeguard.py           ← you may extend this
│   └── templates/                 ← CREATE THIS — all your output goes here
├── examples/                      ← 6 hand-verified ManimGL scenes, your starting point
│   ├── graph_scene.py
│   ├── limit_scene.py
│   ├── matrix_scene.py
│   ├── text_scene.py
│   ├── code_scene.py
│   └── shape_scene.py
└── tests/                         ← 229 existing tests, do not break them
```

---

## Frame / layout system

Frame is 14.22 × 8.0, center at ORIGIN.
Safe zone: x ∈ [-6, 6], y ∈ [-3.5, 3.5]

```
TITLE_ZONE:   y ∈ [2.5, 3.5]   → title.to_edge(UP, buff=0.8)
CONTENT_ZONE: y ∈ [-2.5, 2.0]  → content.center().shift(DOWN * 0.5) when title present
FOOTER_ZONE:  y ∈ [-3.5, -2.5]
```

**Axes sizing is mandatory:**
- No title → `Axes(...).set_width(10).center()`
- With title → `Axes(...).set_width(10).center().shift(DOWN * 0.5)`
- Never `axes.move_to(ORIGIN)` alone — does not resize, causes dead space.

**Multiple annotations near the same anchor:**
```python
# WRONG — both end up at the same position
label1.next_to(axes, RIGHT)
label2.next_to(axes, RIGHT)

# CORRECT — group and arrange
VGroup(label1, label2).arrange(DOWN, buff=0.4).next_to(axes, RIGHT, buff=0.5)
```

**Y-axis labels gotcha:** `include_numbers=True` on y_axis rotates labels 90°.
Always use `include_numbers=False` on y_axis and add labels manually:
```python
for n in [1, 2, 3]:
    lbl = Text(str(n), font_size=22, color=GREY_A)
    lbl.next_to(axes.y_axis.n2p(n), LEFT, buff=0.15)
```

**Every scene MUST end with:**
```python
self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
self.wait(0.5)
```

---

## What to build: manimgen/templates/

### spec_schema.py

Defines ALL valid visual spec types. This is the contract with Session B.

Top-level spec shape:
```json
{
  "mode": "2d",
  "template": "<template name>",
  "title": "string",
  "duration_seconds": 12.5,
  "beats": [ ... ]
}
```

Export a `validate(spec: dict) -> list[str]` function.
Returns a list of error strings (empty list = valid).
Session B imports this.

Also export `KNOWN_TEMPLATES: list[str]` and `SpecValidationError(Exception)`.

---

### base.py

`TemplateScene` base class. Handles:
- Beat sequencer: iterates `spec["beats"]`, calls `render_beat(beat)`, tracks timing
- Title rendering shared across all templates
- Clean exit: always appends FadeOut all + wait(0.5)
- Class method: `from_spec(spec, class_name, output_path) -> str`
  writes the .py file, returns the generated code as a string

Each template generates Python code as strings, assembles them, and writes a .py file.
Do NOT use exec/eval. Generate text, write text.

---

### 2D Templates

#### function_template.py — FunctionTemplate
Starting point: `examples/graph_scene.py`

Beat types:
```
axes_appear:  { x_range, y_range, x_label, y_label }
curve_appear: { expr_str, color, x_range, label }
trace_dot:    { start_x, end_x, color, duration }
annotation:   { text, position: "right"|"left"|"top", color }
transition:   {}
```

Key patterns from graph_scene.py:
- `.set_width(10).center().shift(DOWN * 0.5)` when title present
- `ValueTracker` + `always_redraw` for moving dot
- `VGroup(label1, label2).arrange(DOWN, buff=0.35)` for annotations

---

#### limit_template.py — LimitTemplate
Starting point: `examples/limit_scene.py`

Beat types:
```
axes_appear:   { x_range, y_range }
curve_appear:  { expr_str, color, hole_x, hole_y }
guide_lines:   { limit_x, limit_y }
approach_dot:  { from_side: "left"|"right"|"both", start_x, end_x, color }
annotation:    { value_label, limit_label }
```

Key patterns from limit_scene.py:
- Split curve into two segments around the hole: `x_range=[a, hole_x - 0.08]` and `[hole_x + 0.08, b]`
- Open dot: `Circle(radius=0.1, stroke_color=WHITE, fill_color="#1C1C1C", fill_opacity=1.0)`
- Dashed guide lines: `DashedLine(axes.c2p(0, limit_y), axes.c2p(limit_x, limit_y))`
- Manual y-axis labels (not include_numbers=True — they rotate)
- Annotation grouped: `VGroup(value_tex, limit_tex).arrange(DOWN, buff=0.5).move_to(RIGHT * 3.5)`

---

#### matrix_template.py — MatrixTemplate
Starting point: `examples/matrix_scene.py`

Beat types:
```
plane_appear:     { x_range, y_range }
show_matrix:      { data: [[a,b],[c,d]], position: "UL"|"UR" }
apply_transform:  { data: [[a,b],[c,d]], duration }
show_vector:      { coords: [x, y], color }
annotation:       { text, color }
```

Key patterns from matrix_scene.py:
- `NumberPlane(x_range, y_range, background_line_style={"stroke_color": BLUE_E, "stroke_opacity": 0.5})`
- `plane.animate.apply_matrix(matrix_data)` for the transformation
- `IntegerMatrix(data).scale(0.8).to_corner(UL, buff=0.8)`
- `GrowArrow(arrow)` for vectors

---

#### text_template.py — TextTemplate
Starting point: `examples/text_scene.py`

Beat types:
```
title_only:      { title, subtitle, duration }
bullets:         { items: [str], colors: [str], duration }
highlight:       { index: int, color }
dim_others:      { keep_index: int }
transition:      {}
```

Key patterns from text_scene.py:
- `VGroup(*bullets).arrange(DOWN, aligned_edge=LEFT, buff=0.35).move_to(ORIGIN)`
- `LaggedStart(*[FadeIn(b) for b in bullets], lag_ratio=0.4)`
- `SurroundingRectangle(bullets[i], color=YELLOW, buff=0.1)`

---

#### code_template.py — CodeTemplate
Starting point: `examples/code_scene.py`

Beat types:
```
reveal_lines:    { lines: [{text: str, color: str}], duration }
highlight_line:  { index: int, annotation: str }
dim_others:      { keep_index: int }
transition:      {}
```

Key patterns from code_scene.py:
- `Text(text, font="Courier New", font_size=22, color=color)`
- `VGroup(*lines).arrange(DOWN, aligned_edge=LEFT, buff=0.18).move_to(ORIGIN + DOWN * 0.3)`
- `LaggedStart(*[FadeIn(line) for line in code_lines], lag_ratio=0.15)`
- `line.animate.set_opacity(0.3)` for dimming

---

#### graph_theory_template.py — GraphTheoryTemplate
**No existing example — build from scratch.**
Read `manimgen/output/scenes/section_06.py` and `section_09.py` to see what the LLM
currently produces. Your template should make those patterns deterministic and layout-safe.

Beat types:
```
graph_appear:    {
                   nodes: [[x, y], ...],   ← normalized [-1,1] coords
                   edges: [[i, j], ...],
                   labels: [str] | null,
                   node_color: str,
                   edge_color: str
                 }
highlight_node:  { index: int, color: str, label: str | null, duration: float }
highlight_edge:  { i: int, j: int, color: str, duration: float }
highlight_path:  { path: [int], color: str, duration: float }
traverse_bfs:    { start: int, color: str, duration: float }
traverse_dfs:    { start: int, color: str, duration: float }
annotation:      { text: str, position: "right"|"bottom" }
transition:      {}
```

Key patterns to implement:
- Nodes: `Dot(point, radius=0.15, fill_color=node_color)` in a `VGroup`
- Edges: `Line(start, end, stroke_width=3, color=edge_color)` in a `VGroup`
- Always add edges VGroup BEFORE nodes VGroup so nodes render on top
- Labels: `Text(label, font_size=28).move_to(dot.get_center())`
- Auto-scale: normalized [-1,1] coords → scale to fit [-5,5]×[-3,3] CONTENT_ZONE
- Center graph: `graph_group.center().shift(DOWN * 0.5)` when title present
- Traversal: animate with `self.play(node.animate.set_color(X))` in sequence
- Degree highlight: `FlashAround(node, color=Y)` + `Text("Degree=N").next_to(node, UP)`

---

#### number_line_template.py — NumberLineTemplate

Beat types:
```
line_appear:   { x_range: [min, max, step], include_numbers: bool }
mark_point:    { value: float, color: str, label: str }
mark_interval: { start: float, end: float, color: str }
jump_arrow:    { from_val: float, to_val: float, label: str | null }
annotation:    { text: str }
```

Use ManimGL's `NumberLine(x_range, include_numbers=True)`.

---

#### complex_plane_template.py — ComplexPlaneTemplate

Beat types:
```
plane_appear:        {}
plot_point:          { re: float, im: float, label: str, color: str }
plot_vector:         { re: float, im: float, color: str }
rotate_vector:       { re: float, im: float, angle_degrees: float, duration: float }
show_multiplication: { z1: [re, im], z2: [re, im] }
annotation:          { text: str, position: "right"|"top" }
```

Use ManimGL's `ComplexPlane()` with Re/Im axis labels.

---

#### probability_template.py — ProbabilityTemplate

Beat types:
```
bar_chart:     { categories: [str], values: [float], colors: [str] }
highlight_bar: { index: int, color: str }
sample_space:  { regions: [{label, probability, color}] }
annotation:    { text: str }
```

Use ManimGL's `BarChart` for bar charts.

---

#### geometry_template.py — GeometryTemplate

Beat types:
```
shape_appear: { shape: "circle"|"square"|"triangle"|"polygon", params: {...}, color: str }
label_side:   { shape_index: int, side: str, label: str }
show_angle:   { vertex: [x,y], from_pt: [x,y], to_pt: [x,y], label: str }
brace:        { target_index: int, direction: "down"|"up"|"left"|"right", label: str }
transform:    { from_index: int, to_shape: str, to_params: {...} }
annotation:   { text: str }
```

---

### 3D Templates (use ThreeDScene, not Scene)

**3D key differences:**
- Base class: `ThreeDScene` (from manimlib)
- Camera: `self.frame.set_euler_angles(theta=X * DEGREES, phi=Y * DEGREES)`
- Default orientation: phi=70°, theta=-30°
- 3D objects: `Sphere`, `Torus`, `Cylinder`, `Cone`, `Cube` (all from manimlib)
- 3D axes: `ThreeDAxes(x_range, y_range, z_range)`
- Surfaces: `ThreeDAxes.get_parametric_surface(func, resolution=(30,30))`
- Text stays 2D — position with `.to_corner()` or `.to_edge()`
- No layout zone system — depth testing handles occlusion automatically
- Camera rotation animation: `self.play(self.frame.animate.reorient(theta, phi))`

#### surface_3d_template.py — Surface3DTemplate
Beat types:
```
axes_appear:    { x_range, y_range, z_range }
surface_appear: { expr_str: "sin(x)*cos(y)", color: str, opacity: float }
rotate_camera:  { delta_theta: float, delta_phi: float, duration: float }
trace_curve:    { u_range, color, duration }
annotation:     { text: str }
```

#### solid_3d_template.py — Solid3DTemplate
Beat types:
```
solid_appear:  { shape: "sphere"|"torus"|"cylinder"|"cone"|"cube", params: {...}, color: str }
rotate:        { axis: "x"|"y"|"z", angle_degrees: float, duration: float }
rotate_camera: { delta_theta: float, delta_phi: float, duration: float }
annotation:    { text: str }
```

#### vector_field_3d_template.py — VectorField3DTemplate
Beat types:
```
axes_appear:   { x_range, y_range, z_range }
vector_field:  { func_str: "[-y, x, 0]", color: str }
flow_particle: { start: [x,y,z], color: str, duration: float }
rotate_camera: { delta_theta: float, duration: float }
```

#### parametric_3d_template.py — Parametric3DTemplate
Beat types:
```
axes_appear:  { x_range, y_range, z_range }
curve_appear: { expr_str: "[cos(t), sin(t), t/3]", t_range: [0, 6.28], color: str }
rotate_camera: { delta_theta: float, duration: float }
```

---

## dispatch.py

Create `manimgen/templates/dispatch.py`:

```python
from manimgen.templates.function_template import FunctionTemplate
# ... all imports ...

TEMPLATE_MAP = {
    "function":        FunctionTemplate,
    "limit":           LimitTemplate,
    "matrix":          MatrixTemplate,
    "text":            TextTemplate,
    "code":            CodeTemplate,
    "graph_theory":    GraphTheoryTemplate,
    "number_line":     NumberLineTemplate,
    "complex_plane":   ComplexPlaneTemplate,
    "probability":     ProbabilityTemplate,
    "geometry":        GeometryTemplate,
    "surface_3d":      Surface3DTemplate,
    "solid_3d":        Solid3DTemplate,
    "vector_field_3d": VectorField3DTemplate,
    "parametric_3d":   Parametric3DTemplate,
}

def render_spec(spec: dict, class_name: str, output_path: str) -> str:
    template_cls = TEMPLATE_MAP[spec["template"]]
    return template_cls.from_spec(spec, class_name, output_path)
```

---

## from_spec() contract

Every template implements:
```python
@classmethod
def from_spec(cls, spec: dict, class_name: str, output_path: str) -> str:
    """
    Render spec to a ManimGL .py file.
    Returns the generated code as a string.
    Writes code to output_path.
    """
```

Generated .py files must:
1. `from manimlib import *` — only import
2. Exactly one Scene class with the given `class_name`
3. Total duration ≈ `spec["duration_seconds"]` ± 0.5s
4. Pass `codeguard.validate_scene_code()` with zero errors

---

## Interface contract with Session B

Session B calls your code like this:
```python
from manimgen.templates.spec_schema import validate, SpecValidationError, KNOWN_TEMPLATES
from manimgen.templates.dispatch import render_spec

errors = validate(spec)
if not errors:
    code = render_spec(spec, class_name, output_path)
```

**Commit `spec_schema.py` and `dispatch.py` (even as stubs) FIRST** so Session B
can start writing their validation and dispatch code without waiting.

---

## Tests

Add `tests/test_templates.py`:
- Each template's `from_spec()` produces Python that passes `ast.parse()`
- Each template's `from_spec()` produces code that passes `codeguard.validate_scene_code()`
- `render_spec()` with unknown template key raises `KeyError`
- 3D templates generate `ThreeDScene` not `Scene` in the output
- `validate()` returns errors for missing required fields
- `validate()` returns empty list for valid specs

Run existing tests after any change: `python3 -m pytest tests/ -v`
