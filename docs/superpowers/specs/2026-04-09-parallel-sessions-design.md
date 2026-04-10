# Parallel Sessions Design — 2026-04-09

## Goal
Two independent Claude Code sessions run in parallel to (1) harden the pipeline and (2) expand its visual repertoire. Neither session touches the other's files.

---

## Session 1: `feature/pipeline-reliability`

**Objective:** Fix the three root causes of visible quality failures. No new features.

### Tasks (in order)
1. Merge `feature/swap-timing-fixes` → `main`, pull, branch `feature/pipeline-reliability`
2. **Codeguard: loop timing detector** — new static pass detects `self.wait(` after a loop body with no accumulator variable. Emit structured warning + fix hint. New test in `tests/test_codeguard_loop_timing.py`.
3. **Layout checker: structured feedback** — rewrite `validator/prompts/layout_checker_system.md` to return `ISSUE | CAUSE | FIX` lines instead of prose. Update `validator/retry.py` to parse and inject as prefixed feedback into fix prompt. New test in `tests/test_layout_checker.py`.
4. **Muxer: visibility** — bump `_WARN_THRESHOLD_SECONDS` to `1.0` in `renderer/muxer.py`. Print per-cue mismatch count in final pipeline summary.

### Files
- `manimgen/validator/codeguard.py`
- `manimgen/validator/retry.py`
- `manimgen/validator/layout_checker.py`
- `manimgen/validator/prompts/layout_checker_system.md`
- `manimgen/renderer/muxer.py`
- `manimgen/tests/test_codeguard_loop_timing.py` (new)
- `manimgen/tests/test_layout_checker.py` (new)

### Do NOT touch
`examples/`, `planner_system.md`, `director_system.md`, `scene_generator.py`

---

## Session 2: `feature/rich-techniques`

**Objective:** Expand the Director's technique repertoire with verified 3D and 2D example scenes and matching prompt entries.

### Tasks (in order)
1. Merge `feature/swap-timing-fixes` → `main`, pull, branch `feature/rich-techniques`
2. **New example scenes** (each with `techniques:` tag in class docstring):
   - `examples/camera_flythrough_scene.py` — ThreeDScene, camera path via frame updater
   - `examples/dot_product_3d_scene.py` — ThreeDAxes, two vectors, angle arc, projection
   - `examples/cross_section_scene.py` — ParametricSurface + horizontal slice plane
   - `examples/value_tracker_tracer_scene.py` — ValueTracker + always-redraw curve tracer
   - `examples/lagged_path_scene.py` — LaggedStart with custom path/arc motion
   - Check/upgrade `examples/number_plane_transform_scene.py` — add ApplyMatrix technique
3. **Planner prompt** — add new technique names to technique table in `planner/prompts/planner_system.md`
4. **Director prompt** — add new technique entries + expand 3D section in `generator/prompts/director_system.md`
5. **Smoke test** — run `manimgen "gradient descent"` end-to-end, observe output quality

### Files
- `manimgen/examples/` (6 files)
- `manimgen/planner/prompts/planner_system.md`
- `manimgen/generator/prompts/director_system.md`

### Do NOT touch
`codeguard.py`, `muxer.py`, `layout_checker.py`, `retry.py`, any test files

---

## Interface contract between sessions
Both sessions branch from the same `main` (post-merge). They communicate only through git — no shared in-flight state. Either can merge to main independently once tests pass.
