# ManimGen — Session Guide

## Session primer
Read `SESSION_2026_04_07.md` at the start of each session — it contains what was done last session, current git state, uncommitted changes, and what to do next.

## What this project is
Automated pipeline: **topic string or PDF → 3Blue1Brown-style animated explainer video with narration.**
Uses an audio-first CUE pipeline where spoken word timestamps drive animation durations — no speed warping.

**Stack:** Python 3.13, ManimGL (3b1b fork), Gemini 2.5 Flash, FFmpeg 8.1, LaTeX, edge-tts, Flask (editor)

**Repo:** `https://github.com/Varkot-dev/videomaking.git` — branch `main`
**314 tests, all passing** (`python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -v`)

---

## Critical: ManimGL vs ManimCommunity
This project uses `manimgl` (3b1b's version), **NOT** `manim` (ManimCommunity).

| Correct (ManimGL) | Wrong (ManimCommunity) |
|---|---|
| `from manimlib import *` | `from manim import *` |
| `ShowCreation(obj)` | `Create(obj)` |
| `Tex(...)` | `MathTex(...)` |
| `self.frame` | `self.camera.frame` |
| `FlashAround(obj)` | `Circumscribe(obj)` |
| `manimgl file.py ClassName -w --hd` | `manim file.py ClassName` |
| `-c "#1C1C1C"` for background color | `--background_color` does NOT exist |

`codeguard.py` auto-fixes many ManimCommunity→ManimGL mismatches.

---

## Project structure

```
manimgen/
├── manimgen/                    # source package
│   ├── cli.py                   # entry: manimgen <topic> | --pdf <file> | --resume
│   ├── llm.py                   # shared LLM client (Gemini/Anthropic toggle)
│   ├── utils.py                 # shared: strip_fencing(), section_class_name()
│   ├── input/
│   │   ├── parser.py            # normalize topic string
│   │   └── pdf_parser.py        # extract text + render pages from PDF
│   ├── planner/
│   │   ├── lesson_planner.py    # LLM → storyboard JSON (with _safe_json_loads)
│   │   ├── cue_parser.py        # parse [CUE] markers → cue_word_indices
│   │   ├── segmenter.py         # word timestamps + cue indices → CueSegment durations
│   │   └── prompts/             # planner_system.md, planner_pdf_system.md
│   ├── generator/
│   │   ├── scene_generator.py   # Director: LLM → one ManimGL Scene per section
│   │   └── prompts/             # director_system.md
│   ├── validator/
│   │   ├── codeguard.py         # static checks + auto-fixes (font_size→scale, banned kwargs, layout smells)
│   │   ├── runner.py            # subprocess manimgl with -c #1C1C1C, logs attempt
│   │   ├── retry.py             # retry loop: codeguard → error-aware fix → LLM fix (budget-capped)
│   │   ├── fallback.py          # styled bullet-point fallback scene (with TTS)
│   │   ├── layout_checker.py    # LLM vision check on rendered frames
│   │   ├── env.py               # render environment vars (LaTeX PATH)
│   │   └── prompts/             # retry_system.md, fallback_system.md, layout_checker_system.md
│   ├── renderer/
│   │   ├── tts.py               # edge-tts with WordBoundary → per-word timestamps
│   │   ├── audio_slicer.py      # full audio → N cue-aligned .m4a slices (AAC, sample-accurate)
│   │   ├── cutter.py            # cut rendered section .mp4 into per-cue clips (parallel FFmpeg)
│   │   ├── muxer.py             # audio+video mux (pad-only, no speed warp)
│   │   └── assembler.py         # normalize 1920x1080@30fps, xfade between sections
│   └── editor/
│       ├── server.py            # Flask UI for clip review, reorder, trim, export
│       └── templates/editor.html
├── examples/                    # hand-written verified ManimGL scenes (Director few-shot reference)
│                                # Each scene has `techniques: <name>, <name>` as first line of class
│                                # docstring — scene_generator._index_examples() indexes them at runtime
├── tests/                       # 337 pytest tests
├── config.yaml                  # LLM provider, TTS config, render quality
├── requirements.txt
└── setup.py                     # console_scripts: manimgen, manimgen-edit
```

---

## Pipeline flow (Director architecture — current)

```
topic / PDF
  → parse_input() / pdf_parser
  → plan_lesson()              LLM → storyboard JSON with:
                                 - narration with [CUE] markers
                                 - cues[]: [{index, visual}] per cue (pixel-level descriptions)
  → cue_parser.parse_cues()    strips [CUE] → clean narration + cue_word_indices
  → plan saved to manimgen/output/plan.json

For each section:
  1. TTS (edge-tts WordBoundary)  → full .mp3 + per-word timestamps (sub-ms)
  2. segmenter.compute_segments() → exact duration per cue from word onset times
  3. audio_slicer.slice_audio()   → section_01_cue00.mp3, _cue01.mp3, ...

  4. generate_scenes(section, cue_durations=[...])
       → ONE LLM call (Director) with storyboard + all cue durations
       → Director writes ONE complete Python Scene class
       → Scene has self.wait() pauses at each cue boundary
       → codeguard auto-fixes the code
       → saved as section_01.py

  5. run_scene(scene_path, class_name) → ONE rendered section_01.mp4
  6. retry_scene() on failure → fallback_scene() if all retries fail

  7. cutter.cut_video_at_cues() → N per-cue video clips (FFmpeg re-encode)
  8. mux_audio_video() per cue → section_01_cue00.mp4, _cue01.mp4, ...

→ assemble_video()  normalize + xfade transitions → final .mp4
→ manimgen-edit     browser UI for clip reorder/trim/export
```

**Key architectural decisions:**
- **One scene per section, not per cue.** The Director generates a single continuous animation. Axes build once. Cues animate on top of what's already present. No restarts.
- **Template engine is GONE.** The Director writes ManimGL Python directly. Visual variety comes from the storyboard descriptions, not from switching templates.
- **Storyboard-level planner.** The planner outputs pixel-level visual descriptions per cue — not concept descriptions. "Yellow curve y=x² with red dot at x=-1.5 moving right" not "show a parabola".
- **codeguard is the safety net.** precheck_and_autofix() runs on the generated code string before saving. Catches import errors, API mismatches, and axes height overflow.

---

## When to use the LLM vs hardcode

**Hardcode (no API calls) when:**
- Testing ManimGL rendering behaviour — camera, surfaces, animations, depth, opacity
- Verifying a new 3D API works before adding it to the pipeline
- Writing example scenes for the Director's few-shot reference (`examples/`)
- All of the above: just write a scene directly in `examples/` and render with `manimgl file.py ClassName -c "#1C1C1C" -w`

**Use the LLM when:**
- Testing Director *output quality* — does it generate visually correct code for a given storyboard?
- Running the full pipeline end-to-end on a real topic or PDF
- The existing pytest suite already mocks LLM calls for correctness tests — real API only needed for quality evaluation

**Rule:** if you're not evaluating LLM output quality, hardcode. Writing scenes manually is faster, free, and gives exact control over what's being tested.

---

## Running the pipeline

```bash
# From manimgen/ project root
export GEMINI_API_KEY=...       # or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic

manimgen "binary search"        # topic mode (up to 6 sections)
manimgen --pdf notes.pdf        # PDF mode (up to 8 sections)
manimgen --resume               # resume from cached plan.json (skips LLM planning call)

# Cost control
MANIMGEN_MAX_RETRY_LLM_CALLS=1 manimgen "topic"   # cap retry LLM calls to 1 per scene
MANIMGEN_MAX_RETRY_LLM_CALLS=0 manimgen "topic"   # local fixes only, no paid retries

manimgen-edit                   # launch clip editor (defaults to muxed/ dir)

# Run tests (zero cost)
python3 -m pytest tests/ -v
```

**Output locations:**
- Final video: `manimgen/output/videos/<title>.mp4`
- Muxed clips: `manimgen/output/muxed/`
- Audio + timestamps: `manimgen/output/audio/`
- Scene code + logs: `manimgen/output/scenes/`, `manimgen/output/logs/`
- Editor exports: `manimgen/output/videos/exports/`

---

## LLM provider toggle

Resolution order (first wins):
1. `LLM_PROVIDER` env var
2. `llm_provider` in `config.yaml`
3. Default: `"gemini"`

| Provider | Model | SDK |
|---|---|---|
| `gemini` | `gemini-2.5-flash` | `google.genai` (NOT the deprecated `google.generativeai`) |
| `anthropic` | `claude-sonnet-4-6` | `anthropic` |

---

## Video quality systems (current)

### 1. Prompt architecture (Director model)
- `planner/prompts/planner_system.md`: outputs storyboard with `cues[{index, visual}]`. Each `visual` starts with `Technique: <name>` and gives exact ManimGL-implementable descriptions (specific objects, colors, positions, motion). NOT vague concept descriptions.
- `generator/prompts/director_system.md`: ManimGL API reference, layout zone rules, banned patterns, technique table. Kept tight — no inline scaffolds.
- `examples/`: 22 hand-written verified ManimGL scenes (includes 3D scenes). Each has a `techniques:` tag in its docstring — `scene_generator.py` reads this tag to select relevant examples per section automatically. No hardcoded mappings in code.
- **DELETED:** `generator/prompts/spec_system.md`, `templates/` directory, `spec_schema.py` — the template engine is gone.

**Example technique tag format** (first line inside class docstring):
```
techniques: sweep_highlight, stagger_reveal
```
`scene_generator._index_examples()` reads this to build the technique→file index at runtime.

### 2. Dark background
All `manimgl` subprocess calls use `-c "#1C1C1C"`. This is enforced in `runner.py`, `retry.py`, and `fallback.py`.
**WARNING:** The flag is `-c`, NOT `--background_color` (which does not exist in manimgl CLI and silently crashes).

### 3. Spatial layout enforcement
Three layers of defense against text-over-diagram overlap:
- **Prompt:** Zone model (TITLE_ZONE y∈[2.5,3.5], CONTENT_ZONE y∈[-2.5,2.0], FOOTER_ZONE y∈[-3.5,-2.5])
- **User prompt:** 9 mandatory layout rules per generation call
- **Static analysis:** `codeguard._check_layout_smells()` — warns on axes without sizing, labels inside axes, missing cleanup

### 4. codeguard.py auto-fixes (token-free)
- `font_size=` on `Tex()` → converts to `.scale(font_size/32)` instead of just stripping
- Banned kwargs: `tip_length`, `tip_width`, `tip_shape`, `corner_radius`, `scale_factor`, `target_position`
- `font=` on `Tex()`/`TexText()` → removed
- `Circumscribe` → `FlashAround`
- Color name fixes: `DARK_GREY` → `GREY_D`, etc.
- `color_gradient` int cast
- `.animate` mixed with `FadeIn`/`FadeOut` → split into separate `self.play()` calls
- Error-aware fixes driven by runtime traceback (`apply_error_aware_fixes`)

### 5. TTS voice
- Engine: `edge-tts` (free, local)
- Voice: `en-US-AndrewMultilingualNeural` (natural prosody)
- Speed: `+5%`
- Word timestamps via `WordBoundary` mode for CUE alignment

### 6. Muxer (pad-only)
- Video longer than audio → **trim video** to audio duration (no silent tails)
- Audio longer than video → freeze last frame (`tpad`)
- Never warps speed. Logs warning if mismatch > 0.5s.

### 7. Assembler
- Normalizes all clips to `1920x1080@30fps`
- 0.3s `xfade` + `acrossfade` between sections
- Hard cuts within a section (between cue clips)

### 8. Fallback scenes
- Styled bullet points showing section's `key_objects`, not "(animation unavailable)"
- TTS runs on fallback scenes (narration still plays)
- Duration estimated from narration word count

---

## Key rules for development

1. **Never use `--background_color`** — the correct flag is `-c "#1C1C1C"`
2. **ManimGL `Tex()` accepts `font_size=`** — it is a real parameter (default 48) handled internally via `.scale()`. `Text()` also accepts it. Do NOT convert `Tex(r"x", font_size=48)` to `.scale()` — that double-scales.
3. **All prompts live as `.md` files** in `prompts/` dirs — never inline Python strings
4. **Retry order:** codeguard auto-fix → error-aware fix (token-free) → LLM fix (budget-capped) → fallback
5. **Token budget:** `MAX_LLM_FIX_CALLS` (default 1) limits paid retry calls
6. **JSON from LLMs can have bad escapes** — `_safe_json_loads` in `lesson_planner.py` handles `\e`, `\s`, etc.
7. **Section caps:** 6 (topic) / 8 (PDF), enforced by `_cap_sections()`
8. **Plan caching:** `--resume` flag skips LLM planning call, reads `manimgen/output/plan.json`
9. **Log everything:** every attempt's code and stderr goes to `manimgen/output/logs/`
10. **codeguard is the first line of defense** — extend it for any new known-bad pattern before touching prompts
11. **Adding a new example scene:** add the file to `examples/`, add `techniques: <name>, <name>` as the first line of the class docstring. `scene_generator.py` picks it up automatically — no code changes needed.
12. **Master Guidelines (`MASTER GUIDELINES.md`)** — no hardcoded mappings in code, no duplicate sources of truth, no speculative abstractions. Data lives in data (files, config); code reads it.
13. **No duplicate source files** — the package lives exclusively in `manimgen/manimgen/`. Never create top-level mirrors of `cli.py`, `llm.py`, `validator/`, etc. at `manimgen/` root level. `find_packages()` picks up both and causes import confusion.

---

## ManimGL API gotchas

### Axis tick label font size
`font_size` is NOT valid in `axis_config` directly. Use:
```python
axes = Axes(
    x_range=[-3, 3, 1],
    axis_config={
        "include_numbers": True,
        "decimal_number_config": {"font_size": 24},
        "color": GREY_B,
    },
)
```

### Axes sizing — use width/height params (ManimGL), NOT x_length/y_length (ManimCommunity)
```python
# ManimGL Axes uses width= and height= (NOT x_length/y_length — that's ManimCommunity)
axes = Axes(
    x_range=[-2, 3, 1], y_range=[-1, 5, 1],
    width=7, height=4.5,   # ← hard-caps screen size, prevents title overflow
    axis_config={"color": GREY_B, "include_tip": True},
    x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
    y_axis_config={"include_numbers": False},
).center().shift(DOWN * 0.8)  # always shift down when title present
```
codeguard auto-fixes `x_length=` → `width=` and `y_length=` → `height=` if the LLM uses ManimCommunity syntax.

### Multiple annotations on same anchor
Always: `VGroup(...).arrange(DOWN, buff=0.4)` — never two independent `.next_to(same_anchor)` calls

### 3D scenes — verified from manimlib source (`scene.py`, `three_dimensions.py`, `camera_frame.py`, `surface.py`)

**RULE: Before writing any 3D API call, read the source. Never assume from docs or task descriptions.**

#### Camera
```python
self.frame.reorient(-30, 70)          # theta_deg, phi_deg — takes degrees directly, no * DEGREES
self.frame.add_ambient_rotation(angular_speed=0.2)  # param is angular_speed, NOT speed
self.frame.clear_updaters()           # call before FadeOut to stop rotation

# Preferred orbit pattern — camera moves, objects stay fixed in world space
# This means ALL objects appear to rotate together correctly
self.frame.add_updater(lambda m, dt: m.increment_theta(-0.15 * dt))
```

#### Surfaces
```python
# ThreeDAxes — confirmed in coordinate_systems.py:535
axes = ThreeDAxes(x_range=[-3,3,1], y_range=[-3,3,1], z_range=[-2,2,1])
axes.add_axis_labels()  # adds x/y/z labels, built-in method

# ParametricSurface — uv_func returns a raw 3D np.array, NOT axes.c2p()
# Confirmed in surface.py:268 — uv_func(u, v) → Iterable[float]
surface = ParametricSurface(
    lambda u, v: np.array([u, v, np.sin(u) * np.cos(v)]),
    u_range=(-PI, PI),
    v_range=(-PI, PI),
    resolution=(32, 32),
)

# set_color_by_xyz_func — takes a GLSL string, NOT a Python lambda
# Confirmed in mobject.py:2002 — glsl_snippet: str
surface.set_color_by_xyz_func("z", min_value=-1.0, max_value=1.0, colormap="viridis")

# set_shading params — confirmed in mobject.py:1442
# set_shading(reflectiveness, gloss, shadow) — NOT (diffuse, specular, ambient)
surface.set_shading(0.8, 0.5, 0.5)

# SurfaceMesh — confirmed in three_dimensions.py:31
# Add mesh as child so it moves with the surface
mesh = SurfaceMesh(surface, resolution=(12, 12))
mesh.set_stroke(WHITE, 0.8, opacity=0.4)
surface.add(mesh)
```

#### Transparency (glass / interior reveal)
```python
# Correct approach for see-through surfaces:
# 1. deactivate_depth_test() so the surface doesn't occlude objects behind it
# 2. set_opacity() to a low uniform value (0.08–0.15 for glass effect)
# Do NOT use per-point opacity arrays — creates hard walls, not transparency
sphere.deactivate_depth_test()
self.play(sphere.animate.set_opacity(0.08), run_time=1.5)

# To show objects INSIDE a closed surface:
# - self.add(inner_object) BEFORE adding the outer shell
# - Camera orbit (frame updater) makes both appear to rotate together
# - deactivate_depth_test() on the shell before going transparent
```

#### Text and labels in ThreeDScene
```python
# Pin 2D text to the screen — otherwise it renders in 3D world space and looks wrong
label.fix_in_frame()   # correct method — confirmed in mobject.py:1924
# add_fixed_in_frame_mobjects() does NOT exist in this version of manimlib
```

#### Confirmed 3D shapes (three_dimensions.py)
```python
Sphere(radius=1.0)
Torus(r1=2.0, r2=0.5)          # r1=major radius, r2=tube radius
Cylinder(height=2.5, radius=0.8)
Cone(...)                        # subclass of Cylinder
SurfaceMesh(surface, resolution=(rows, cols))
```

**What does NOT exist / is WRONG:**
- `self.frame.set_euler_angles(theta * DEGREES, ...)` — use `reorient(theta_deg, phi_deg)`
- `set_color_by_xyz_func(lambda x, y, z: z)` — crashes, takes a GLSL string not a callable
- `axes.c2p(u, v, z)` inside ParametricSurface uv_func — uv_func returns raw world coords
- `add_ambient_rotation(speed=...)` — kwarg is `angular_speed=`, not `speed=`
- `add_fixed_in_frame_mobjects(label)` — use `label.fix_in_frame()` instead
- `TexturedSurface` with remote URLs — SSL cert errors on this machine; use local files only

#### 3b1b's own SurfaceExample pattern (example_scenes.py — authoritative reference)
```python
# Mesh stroke on surfaces
mob.mesh = SurfaceMesh(mob)
mob.mesh.set_stroke(BLUE, 1, opacity=0.5)
mob.add(mob.mesh)

# Camera orbit
self.frame.add_updater(lambda m, dt: m.increment_theta(-0.1 * dt))

# Light source manipulation
light = self.camera.light_source
self.play(light.animate.move_to(3 * IN), run_time=5)
```

---

## Fixed bugs (in addition to A/V sync)

### Cue index off-by-one — planner cues[] has N entries for N [CUE] markers, but N+1 segments exist
- **Root cause:** The LLM was told "2-4 [CUE] markers per section" but wasn't told that `cues[]` needs N+1 entries — one per segment, including the opening segment before the first `[CUE]`. So `cues[0]` always described what should be the second visual, and the last cue always had `visual: ""`.
- **Fix:** `planner_system.md` now explicitly states cues[] must have (number of [CUE] markers + 1) entries, with `index 0` covering the opening segment. Example updated to match. `_extract_cues` now warns and synthesizes a fallback visual (from section title + narration) instead of passing `visual: ""` to the Director.

## Known issues / next steps

### Visual feedback loop (partially fixed — 2026-04-08)
Root cause identified from Section02Scene.mp4: rendered scenes with visual defects ship as final output. Status:

1. **Layout checker feedback wired** ✓ — `retry.py` now feeds structured visual feedback back into an LLM fix call within the existing retry budget.
2. **Only one frame sampled** (`layout_checker.py`) — extracts t=1.0s only. Mid-animation defects (swaps, transitions) are invisible. Fix: use `ffprobe` to get duration, sample frames at 25%/50%/75%, send all in one `chat()` call.
3. **Layout checker prompt returns prose, not actionable feedback** (`layout_checker_system.md`) — returns "blue rectangle too wide" but the retry loop needs "caused by SurroundingRectangle on a mutating VGroup — recreate after each swap." Fix: rewrite prompt to return structured `ISSUE/CAUSE/FIX` lines the LLM can act on directly.

**Files to change:** `layout_checker_system.md`, `layout_checker.py`, new `tests/test_layout_checker.py`

**What was ruled out:** Adding more Director prompt rules or example scenes does not scale — no finite set of examples covers all animation types for arbitrary PDFs. The vision layer is the only general solution.

### Codeguard missing patterns — FIXED 2026-04-08 (branch feature/director-examples)
All four high-priority patterns that caused 60% fallback rate in "bubble sort" run are now fixed:

1. **`self.play(SurroundingRectangle(...))` without ShowCreation** — depth-aware `_wrap_bare_rect_in_show_creation()` in `codeguard.py` handles nested args correctly (e.g. `SurroundingRectangle(Text("x"), color=YELLOW)`).
2. **`Tex(r"\text{...}")` outer wrapper** — `_strip_outer_text_wrapper()` strips the wrapper. Mid-expression `\text{}` left alone.
3. **`vgroup[i] = value` VGroup item assignment** — added to `_BANNED_PATTERNS`. Not auto-fixable; retry prompt now has the explanation.
4. **Double-scale bug** — removed the `font_size=` → `.scale()` conversion from `apply_error_aware_fixes()`. `Tex(font_size=48)` is valid, was being double-scaled.

Director prompt (`director_system.md`), retry prompt (`retry_system.md`), and `scene_generator.py` hardcoded rule also corrected. 5 new example scenes added to `examples/` covering sweep_highlight, stagger_reveal, equation_morph, brace_annotation, fade_reveal. 337 tests pass.

### Other known issues
4. **Cue-word tokenization mismatch risk:** `cue_parser` uses `str.split()` word counts; edge-tts may tokenize differently. No integration test guards this yet.
5. **PDF rendering:** Currently renders every page to PNG unconditionally, even text-heavy PDFs. Should use 3-way logic (text-only / image-only / mixed).
6. **No single integration test** for the full plan → TTS → slice → render → mux → assemble pipeline (only unit tests per module).
7. **layout_checker.py uses LLM vision** — costs money per rendered frame check. Currently Gemini (cheaper). Consider making opt-in or running only on non-fallback scenes.

---

## A/V sync — fixed bugs (covered by test_pipeline_contracts.py)

1. **Cue 0 silence** — segmenter measures cue 0 duration from `0.0` (not word onset), matching what the slicer actually cuts. Pre-speech silence preserved.
2. **Last-syllable clipping** — segment boundary uses `word.end` of last word in cue, not `word.start` of next. Last syllable never cut off.
3. **MP3 stream copy drift** — slicer re-encodes to AAC (sample-accurate cuts, not ~26ms frame-boundary drift). Output is `.m4a`.
4. **tpad wrong duration** — muxer `tpad` uses the gap (`audio - video`), not total audio duration.
5. **Sample rate drift** — assembler enforces `-ar 48000` in all three re-encode paths.

---

## Testing (zero cost)

```bash
python3 -m pytest tests/ -v                    # all 314 tests (excl. LLM-calling tests)
python3 -m pytest tests/test_codeguard.py -v   # just static fixes
python3 -m pytest tests/test_pipeline_contracts.py -v   # A/V sync contracts
python3 -m pytest tests/test_cue_parser.py tests/test_segmenter.py tests/test_audio_slicer.py -v  # CUE pipeline
python3 -m py_compile manimgen/cli.py           # quick syntax check
```

Zero-cost smoke test of CUE pipeline (TTS is free, hand-crafted scenes):
```bash
python3 -u <<'SCRIPT'
from manimgen.planner.cue_parser import parse_cues
from manimgen.renderer.tts import generate_narration, save_timestamps, get_audio_duration
from manimgen.planner.segmenter import compute_segments
from manimgen.renderer.audio_slicer import slice_audio

text = "Start here. [CUE] Now this happens. [CUE] And we finish."
clean, cues = parse_cues(text)
_, ts = generate_narration(clean, "/tmp/test.mp3")
segs = compute_segments(ts, cues, get_audio_duration("/tmp/test.mp3"))
slices = slice_audio("/tmp/test.mp3", segs, "/tmp", "test")
print(f"Segments: {len(segs)}, Slices: {len(slices)}")
for s in segs: print(f"  Cue {s.cue_index}: {s.duration:.2f}s")
SCRIPT
```
