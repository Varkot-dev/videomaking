# Session 2 — Rich Techniques

## Context
Project: `manimgen` — automated pipeline: topic → 3Blue1Brown-style video.
Repo: `https://github.com/Varkot-dev/videomaking.git`
Working dir: `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/`
Read `manimgen/CLAUDE.md` before touching any code — it has the full verified ManimGL API (3D camera, surfaces, text pinning, codeguard rules).

## Setup
```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
git checkout main
git merge feature/swap-timing-fixes   # if not already merged
git pull
git checkout -b feature/rich-techniques
```

## How example scenes work
- Each scene in `examples/` is a self-contained ManimGL file.
- The **first line of the class docstring** must be: `techniques: <name>, <name>`
- `scene_generator._index_examples()` reads this tag at runtime — no code changes needed.
- Render each scene to verify before committing: `manimgl examples/<file>.py ClassName -c "#1C1C1C" -w`

## Tasks

### 1. New example scenes
Write and render each. Use only verified ManimGL API from `CLAUDE.md`.

**3D scenes:**

`examples/camera_flythrough_scene.py` — techniques: `camera_flythrough`
- ThreeDScene with ThreeDAxes + a ParametricSurface
- Camera moves along a path: sequence of `self.frame.reorient(theta, phi)` calls with `run_time`
- Label pinned with `label.fix_in_frame()`

`examples/dot_product_3d_scene.py` — techniques: `dot_product_3d`
- ThreeDScene, two 3D vectors (Arrow3D or line from origin), angle arc between them
- Show projection of one onto the other (dashed line)
- Camera orbits via `self.frame.add_updater(lambda m, dt: m.increment_theta(-0.1 * dt))`

`examples/cross_section_scene.py` — techniques: `cross_section_3d`
- ParametricSurface (e.g. paraboloid z = x²+y²)
- A horizontal Rectangle plane that moves up the z-axis via ValueTracker
- Shows the intersection circle growing as the plane rises

**2D scenes:**

`examples/value_tracker_tracer_scene.py` — techniques: `value_tracker_tracer`
- Axes + parametric curve
- ValueTracker `t` drives a dot along the curve via `always_redraw`
- Animate `t` from 0 to 2π with `self.play(t.animate.set_value(TAU), run_time=4)`

`examples/lagged_path_scene.py` — techniques: `lagged_path`
- 8–12 dots that travel from off-screen along arc paths to final positions using `MoveAlongPath`
- `LaggedStart` with `lag_ratio=0.15`

`examples/number_plane_transform_scene.py` — check if it already exists; if so upgrade it.
- NumberPlane + `ApplyMatrix([[2,1],[0,1]])` transformation
- Show a vector before and after — techniques: `number_plane_transform`, `apply_matrix`

### 2. Update planner prompt
**File:** `manimgen/planner/prompts/planner_system.md`

Add the new technique names to the technique table (find the existing table and append):
`camera_flythrough`, `dot_product_3d`, `cross_section_3d`, `value_tracker_tracer`, `lagged_path`, `apply_matrix`

One line each: name + one-sentence description of when to use it.

### 3. Update director prompt
**File:** `manimgen/generator/prompts/director_system.md`

- Add a technique entry for each new technique (name, pattern summary, 3–5 line code snippet).
- Expand the 3D section: add flythrough pattern (sequence of `reorient` calls) and progressive build pattern (add objects one at a time with `self.play(FadeIn(obj))`).

### 4. Smoke test
```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
MANIMGEN_MAX_RETRY_LLM_CALLS=1 python3 -m manimgen.cli "gradient descent"
```
Observe: does the planner use any new techniques? Do new scenes render without fallback?

## Verification
```bash
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -v
```

## Do NOT touch
`codeguard.py`, `muxer.py`, `layout_checker.py`, `retry.py`, any `tests/` files
