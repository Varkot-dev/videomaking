# ManimGen — Session B: Spec Generator

## What you're building

The ManimGen pipeline currently works like this:
```
section dict → LLM → raw Python ManimGL code → manimgl render
```

This produces broken layouts because the LLM writes raw positional code
from scratch every time. You're replacing the LLM call with a two-step process:

New architecture:
```
section dict → LLM → visual spec JSON → dispatch.render_spec() → .py file → manimgl
```

You own the LEFT SIDE: the LLM prompt that produces JSON specs, the validation,
the dispatch call, and the retry/runner plumbing for 3D support.

---

## READ THIS FIRST: dependency on Session A

Before modifying `scene_generator.py`, Session A must commit at minimum:
- `manimgen/templates/spec_schema.py` — exports `validate()`, `KNOWN_TEMPLATES`, `SpecValidationError`
- `manimgen/templates/dispatch.py` — exports `render_spec(spec, class_name, output_path) -> str`

Pull when ready:
```bash
git fetch origin feature/template-engine
git merge origin/feature/template-engine
```

While waiting, read all files listed below and write the new LLM prompt and
`_validate_spec()` stubs (you can stub `render_spec` locally until Session A commits).

---

## Files to read before starting

```
manimgen/manimgen/generator/scene_generator.py     ← you rewrite this
manimgen/manimgen/generator/prompts/generator_system.md  ← replace this
manimgen/manimgen/generator/prompts/rules_core.md  ← keep for reference
manimgen/manimgen/validator/runner.py              ← extend for 3D timeout
manimgen/manimgen/validator/retry.py               ← extend for spec retries
manimgen/manimgen/validator/codeguard.py           ← extend for spec-level checks
manimgen/manimgen/validator/fallback.py            ← fallback still works unchanged
manimgen/manimgen/llm.py                           ← shared LLM client (do not change)
manimgen/manimgen/cli.py                           ← pipeline orchestrator (do not change)
manimgen/manimgen/planner/lesson_planner.py        ← copy _safe_json_loads pattern from here
```

---

## What the current code does (what you're replacing)

`scene_generator.py:generate_scenes()` currently:
1. Loads `generator_system.md` as system prompt
2. Builds a user message with section title, visual_description, key_objects, duration
3. Injects 9 hardcoded layout rules into every message
4. Appends up to 3 truncated example .py files from `examples/` as style references
5. Calls `chat(system, user)` → gets raw Python code back
6. Strips markdown fencing
7. Writes to `output/scenes/<section_id>.py`
8. Returns `(code, class_name, scene_path)`

---

## What generate_scenes() becomes

```python
def generate_scenes(section, cue_index, total_cues, duration_seconds) -> (code, class_name, path):
    # Step 1: LLM produces a visual spec JSON (small, structured, no Python)
    spec = _generate_spec(section, cue_index, total_cues, duration_seconds)

    # Step 2: Validate the spec
    _validate_spec(spec)

    # Step 3: Template engine renders the .py file (zero LLM, zero variance)
    from manimgen.templates.dispatch import render_spec
    code = render_spec(spec, class_name, scene_path)

    return code, class_name, scene_path
```

The function signature and return type stay IDENTICAL — `cli.py` must not need
any changes at all.

---

## Changes to scene_generator.py

### _generate_spec()

New LLM call. The LLM outputs JSON, not Python.

```python
def _generate_spec(section, cue_index, total_cues, duration_seconds) -> dict:
    system = _load_spec_system_prompt()   # loads new spec_system.md
    user_message = f"""
Section title: {section['title']}
Visual description: {section['visual_description']}
Key objects: {', '.join(section.get('key_objects', []))}
Duration: {duration_seconds:.2f} seconds

Pick the most appropriate template. Output only valid JSON.
"""
    if cue_index is not None and total_cues and total_cues > 1:
        user_message += f"\nCUE: This is segment {cue_index + 1} of {total_cues}. Animate only the relevant part."

    raw = chat(system=system, user=user_message)
    return _safe_json_loads(_strip_fencing(raw))
```

Copy `_safe_json_loads` and `_strip_fencing` from `lesson_planner.py` — they already
handle LLM quirks (markdown fencing, bad escape sequences like `\e`, `\s`).

Also save the spec to `output/specs/<section_id>.json` for debugging.

### _validate_spec()

```python
def _validate_spec(spec: dict) -> None:
    from manimgen.templates.spec_schema import validate, SpecValidationError
    errors = validate(spec)
    if errors:
        raise SpecValidationError("\n".join(errors))
```

On `SpecValidationError`, retry.py will re-call `_generate_spec()` with error
context (see retry.py changes below) — not try to fix Python code.

---

## New LLM prompt: spec_system.md

Create `manimgen/generator/prompts/spec_system.md`.

This replaces `generator_system.md` for the spec generation call.
It must be SHORT — no layout rules, no ManimGL API, no Python examples.
Just: what templates exist, what JSON shape to output, and 2-3 minimal examples.

### Template types (embed this in the prompt)

**2D templates** (set `"mode": "2d"`):

| Template name    | Use when                                      |
|------------------|-----------------------------------------------|
| `text`           | conceptual intro, definitions, bullet points  |
| `function`       | plotting f(x) curves, tracing points          |
| `limit`          | limits, continuity, approaching values        |
| `matrix`         | linear transformations, grid deformation      |
| `code`           | algorithms, pseudocode, step-by-step logic    |
| `graph_theory`   | nodes and edges, BFS/DFS, graph structures    |
| `number_line`    | sequences, intervals, modular arithmetic      |
| `complex_plane`  | complex numbers, rotation, Fourier            |
| `probability`    | distributions, sample spaces, bar charts      |
| `geometry`       | triangles, circles, proofs, angles, braces    |

**3D templates** (set `"mode": "3d"`):

| Template name      | Use when                                    |
|--------------------|---------------------------------------------|
| `surface_3d`       | z = f(x,y) surfaces, multivariable calc     |
| `solid_3d`         | spheres, tori, cylinders, 3D geometry       |
| `vector_field_3d`  | flow fields, electromagnetism               |
| `parametric_3d`    | space curves, helices, Lissajous            |

### Beat types by template (embed in prompt)

```
"text":         title_only, bullets, highlight, dim_others, transition
"function":     axes_appear, curve_appear, trace_dot, annotation, transition
"limit":        axes_appear, curve_appear, guide_lines, approach_dot, annotation
"matrix":       plane_appear, show_matrix, apply_transform, show_vector, annotation
"code":         reveal_lines, highlight_line, dim_others, transition
"graph_theory": graph_appear, highlight_node, highlight_edge, highlight_path,
                traverse_bfs, traverse_dfs, annotation, transition
"number_line":  line_appear, mark_point, mark_interval, jump_arrow, annotation
"complex_plane":plane_appear, plot_point, plot_vector, rotate_vector,
                show_multiplication, annotation
"probability":  bar_chart, highlight_bar, sample_space, annotation
"geometry":     shape_appear, label_side, show_angle, brace, transform, annotation
"surface_3d":   axes_appear, surface_appear, rotate_camera, trace_curve, annotation
"solid_3d":     solid_appear, rotate, rotate_camera, annotation
"vector_field_3d": axes_appear, vector_field, flow_particle, rotate_camera
"parametric_3d":axes_appear, curve_appear, rotate_camera
```

### Example specs to embed in prompt

**Example 1 — text:**
```json
{
  "mode": "2d",
  "template": "text",
  "title": "Binary Search",
  "duration_seconds": 8.0,
  "beats": [
    { "type": "title_only", "title": "Binary Search", "subtitle": "Find fast in sorted lists", "duration": 2.5 },
    { "type": "bullets", "items": ["Sorted array required", "Divide and conquer", "O(log n) time"], "colors": ["WHITE", "WHITE", "YELLOW"], "duration": 4.0 },
    { "type": "highlight", "index": 2, "color": "YELLOW" }
  ]
}
```

**Example 2 — function:**
```json
{
  "mode": "2d",
  "template": "function",
  "title": "The Parabola",
  "duration_seconds": 12.0,
  "beats": [
    { "type": "axes_appear", "x_range": [-3, 3, 1], "y_range": [-1, 5, 1], "x_label": "x", "y_label": "f(x)", "duration": 1.5 },
    { "type": "curve_appear", "expr_str": "x**2", "color": "YELLOW", "x_range": [-2.5, 2.5], "label": "f(x) = x²", "duration": 2.0 },
    { "type": "trace_dot", "start_x": -2.5, "end_x": 2.5, "color": "RED", "duration": 3.0 },
    { "type": "annotation", "text": "Minimum at x=0", "position": "right", "color": "WHITE" }
  ]
}
```

**Example 3 — graph_theory:**
```json
{
  "mode": "2d",
  "template": "graph_theory",
  "title": "BFS on a Graph",
  "duration_seconds": 15.0,
  "beats": [
    { "type": "graph_appear",
      "nodes": [[-0.5, 0.8], [0.5, 0.8], [-1.0, 0.0], [0.0, 0.0], [1.0, 0.0]],
      "edges": [[0,1],[0,2],[1,4],[2,3],[3,4]],
      "labels": ["A","B","C","D","E"],
      "node_color": "WHITE", "edge_color": "GREY_B",
      "duration": 3.0 },
    { "type": "traverse_bfs", "start": 0, "color": "YELLOW", "duration": 5.0 },
    { "type": "highlight_node", "index": 3, "color": "GREEN", "label": "Found!", "duration": 2.0 }
  ]
}
```

---

## Changes to runner.py

Current timeout is 120s for all scenes. 3D scenes render significantly slower.

Add to `run_scene()`:
```python
def _is_3d_scene(scene_path: str) -> bool:
    with open(scene_path) as f:
        return "ThreeDScene" in f.read()

# In run_scene(), replace the fixed timeout=120 with:
timeout = 300 if _is_3d_scene(scene_path) else 120
```

The manimgl command stays the same for 3D — no flag changes needed.
ManimGL handles ThreeDScene vs Scene automatically.

---

## Changes to retry.py

Current retry flow:
```
1. run scene
2. if fail: apply_error_aware_fixes (token-free)
3. if still fail: LLM code fix (budget-capped)
4. repeat up to MAX_RETRIES=3
```

New retry flow adds a spec-level retry before code-level retries:

```python
MAX_SPEC_RETRIES = 2   # separate budget from code fix retries

def retry_spec(section, cue_index, total_cues, duration_seconds, errors: list[str]) -> dict:
    """Re-ask LLM for a valid spec when validation fails. Budget: MAX_SPEC_RETRIES."""
    # Include the validation errors in the re-ask so LLM knows what to fix
    ...
```

In `retry_scene()`, catch `SpecValidationError` before the render loop and
call `retry_spec()` rather than going into the code-fix loop.

Existing code retry logic (`apply_error_aware_fixes` → LLM code fix) stays
UNCHANGED for render failures.

---

## Changes to codeguard.py

Add a new function for spec-level safety checks:
```python
def validate_spec_safety(spec: dict) -> list[str]:
    """
    Catch dangerous or unreasonable values in a spec before code generation.
    Returns list of error strings (empty = safe).

    Checks:
    - expr_str / func_str fields don't contain: exec, eval, __import__, open, os.
    - node positions are within reasonable bounds (x in [-5,5], y in [-4,4])
    - graph_theory node count <= 50 (more = unrenderable)
    - duration_seconds is between 2 and 120
    - 3D templates have mode == "3d"
    """
```

Call this from `_validate_spec()` in scene_generator.py after the schema validation.

---

## JSON parsing patterns (copy from lesson_planner.py)

```python
def _strip_fencing(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()

def _safe_json_loads(raw: str) -> dict:
    import re, json
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        return json.loads(sanitized)
```

---

## Fallback behavior (no changes needed)

If spec generation fails entirely after all retries (invalid JSON, unknown template,
budget exhausted), fall through to the existing `fallback_scene()` in `fallback.py`.
It produces bullet points from `section["key_objects"]` — no changes needed there.

The existing fallback path in `cli.py` still triggers correctly because your code
raises standard exceptions that `cli.py` already catches.

---

## CLI interface (do not change cli.py)

`cli.py` calls:
```python
code, class_name, scene_path = generate_scenes(
    section,
    cue_index=seg.cue_index,        # int or None
    total_cues=seg.total_cues,      # int or None
    duration_seconds=seg.duration,  # float or None
)
```

Your rewrite MUST keep this exact signature and return type.

---

## LLM client (do not change llm.py)

```python
from manimgen.llm import chat
response = chat(system="...", user="...")
```

Provider resolves automatically: `LLM_PROVIDER` env var → `config.yaml` → default `"gemini"`.
Models: `gemini-2.5-flash` or `claude-sonnet-4-6`.

---

## Tests to write

Add `tests/test_spec_generator.py`:
- `_validate_spec()` raises `SpecValidationError` when `"template"` key is missing
- `_validate_spec()` raises `SpecValidationError` for unknown template name
- `_validate_spec()` raises `SpecValidationError` when `beats` is empty
- `validate_spec_safety()` returns errors for `expr_str` containing `"exec"`
- `validate_spec_safety()` returns errors for `duration_seconds > 120`
- `_is_3d_scene()` returns True for a file containing `ThreeDScene`
- `_is_3d_scene()` returns False for a file containing only `Scene`
- Mock `chat()` to return a known spec JSON; verify `render_spec()` is called

Run existing tests after any change: `python3 -m pytest tests/ -v`

---

## Coordination with Session A

Session A builds `manimgen/templates/`. You depend on:
```python
from manimgen.templates.spec_schema import validate, SpecValidationError, KNOWN_TEMPLATES
from manimgen.templates.dispatch import render_spec
```

Session A will commit stubs early. Until then, stub these locally:
```python
# Temporary stubs until Session A commits
def validate(spec): return []
class SpecValidationError(Exception): pass
KNOWN_TEMPLATES = []
def render_spec(spec, class_name, output_path): return ""
```

Do NOT build any templates yourself — that's Session A's territory.
Coordinate on exact beat field names if anything in the prompt examples
above doesn't match what Session A defines in spec_schema.py.
