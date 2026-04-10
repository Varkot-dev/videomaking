# ManimGen — Session Guide

## What this project is
Automated pipeline: **topic string or PDF → 3Blue1Brown-style animated explainer video with narration.**
Uses an audio-first CUE pipeline where spoken word timestamps drive animation durations — no speed warping.

**Stack:** Python 3.13, ManimGL (3b1b fork), Gemini 2.5 Flash, FFmpeg 8.1, LaTeX, edge-tts, Flask (editor)

**Repo:** `https://github.com/Varkot-dev/videomaking.git` — active branch: `antigravity`
**399 tests, all passing** (`python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q`)

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
| `self.frame.reorient(theta_deg, phi_deg)` | `self.set_camera_orientation(...)` — does NOT exist |
| `manimgl file.py ClassName -w --hd` | `manim file.py ClassName` |
| `-c "#1C1C1C"` for background color | `--background_color` does NOT exist |

`codeguard.py` auto-fixes many ManimCommunity→ManimGL mismatches.

---

## Source package location
The importable package lives at **`manimgen/manimgen/`** (the nested directory). Never create top-level mirrors of source files at `manimgen/` root level — `find_packages()` picks up both and causes import confusion. Always edit files inside `manimgen/manimgen/`.

---

## Project structure

```
manimgen/
├── manimgen/                    # source package (the importable one)
│   ├── cli.py                   # entry: manimgen <topic> | --pdf <file> | --resume
│   ├── llm.py                   # shared LLM client (Gemini/Anthropic toggle, config-driven)
│   ├── utils.py                 # shared: strip_fencing(), section_class_name()
│   ├── input/
│   │   ├── parser.py            # normalize topic string
│   │   └── pdf_parser.py        # extract text + render pages from PDF
│   ├── planner/
│   │   ├── lesson_planner.py    # research_topic() + plan_lesson() → storyboard JSON
│   │   ├── cue_parser.py        # parse [CUE] markers → cue_word_indices
│   │   ├── segmenter.py         # word timestamps + cue indices → CueSegment durations
│   │   └── prompts/             # planner_system.md, planner_pdf_system.md, researcher_system.md
│   ├── generator/
│   │   ├── scene_generator.py   # Director: LLM → one ManimGL Scene per section
│   │   └── prompts/             # director_system.md
│   ├── validator/
│   │   ├── codeguard.py         # static checks + auto-fixes (see codeguard section below)
│   │   ├── runner.py            # subprocess manimgl with -c #1C1C1C, logs attempt
│   │   ├── retry.py             # retry loop: codeguard → error-aware fix → LLM fix → fallback
│   │   ├── fallback.py          # styled bullet-point fallback scene (with TTS)
│   │   ├── layout_checker.py    # LLM vision check on rendered frames (multi-frame)
│   │   ├── frame_checker.py     # zero-cost PIL-based black/frozen/clipping detection
│   │   ├── timing_verifier.py   # static AST timing analysis (built, not yet wired in)
│   │   ├── env.py               # render environment vars (LaTeX PATH)
│   │   └── prompts/             # retry_system.md, fallback_system.md, layout_checker_system.md
│   ├── renderer/
│   │   ├── tts.py               # edge-tts with WordBoundary → per-word timestamps
│   │   ├── audio_slicer.py      # full audio → N cue-aligned .m4a slices (AAC, sample-accurate)
│   │   ├── cutter.py            # cut rendered section .mp4 into per-cue clips (parallel FFmpeg)
│   │   ├── muxer.py             # audio+video mux (pad-only, no speed warp)
│   │   └── assembler.py         # normalize 1920x1080@60fps, xfade between sections
│   └── editor/
│       ├── server.py            # Flask UI for clip review, reorder, trim, export
│       └── templates/editor.html
├── examples/                    # hand-written verified ManimGL scenes (Director few-shot reference)
│                                # Each scene has `techniques: <name>, <name>` as first line of class
│                                # docstring — scene_generator._index_examples() indexes them at runtime
├── tests/                       # 399 pytest tests
├── config.yaml                  # LLM provider, model names, TTS config, render quality
├── requirements.txt
└── setup.py                     # console_scripts: manimgen, manimgen-edit
```

---

## Pipeline flow (Director architecture — current)

```
topic / PDF
  → parse_input() / pdf_parser
  → research_topic()            LLM → structured knowledge brief (Panel of Experts prompt)
  → plan_lesson()               LLM → storyboard JSON with:
                                   - narration with [CUE] markers
                                   - cues[]: [{index, visual}] per cue (pixel-level descriptions)
  → cue_parser.parse_cues()    strips [CUE] → clean narration + cue_word_indices
  → plan saved to manimgen/output/plan.json

For each section:
  1. TTS (edge-tts WordBoundary)  → full .mp3 + per-word timestamps (sub-ms)
  2. segmenter.compute_segments() → exact duration per cue from word onset times
  3. audio_slicer.slice_audio()   → section_01_cue00.m4a, _cue01.m4a, ...

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
- **Template engine is GONE.** The Director writes ManimGL Python directly. Visual variety comes from the storyboard descriptions.
- **Storyboard-level planner.** The planner outputs pixel-level visual descriptions per cue — not concept descriptions.
- **codeguard is the safety net.** `precheck_and_autofix()` runs on the generated code string before saving.
- **Render cache with hash sidecar.** Each rendered video has a `.hash` sidecar file storing the topic hash. Stale renders (different topic) are detected and re-rendered automatically.
- **Retry loop always reloads code.** After each fix attempt (codeguard or LLM), the file is always re-read from disk — codeguard may have applied in-place fixes that would otherwise be discarded.

---

## When to use the LLM vs hardcode

**Hardcode (no API calls) when:**
- Testing ManimGL rendering behaviour — camera, surfaces, animations, depth, opacity
- Verifying a new 3D API works before adding it to the pipeline
- Writing example scenes for the Director's few-shot reference (`examples/`)

**Use the LLM when:**
- Testing Director *output quality* — does it generate visually correct code for a given storyboard?
- Running the full pipeline end-to-end on a real topic or PDF

**Rule:** if you're not evaluating LLM output quality, hardcode. Writing scenes manually is faster, free, and gives exact control over what's being tested.

---

## Running the pipeline

```bash
# From manimgen/ project root
GEMINI_API_KEY=<key> MANIMGEN_MAX_RETRY_LLM_CALLS=2 manimgen "gradient descent"
GEMINI_API_KEY=<key> manimgen --pdf notes.pdf
GEMINI_API_KEY=<key> manimgen --resume   # resume from cached plan.json

manimgen-edit   # launch clip editor

# Run tests (zero cost)
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

**API key location:** `manimgen/.env` (GEMINI_API_KEY=...)

**Output locations:**
- Final video: `manimgen/output/videos/<title>.mp4`
- Muxed clips: `manimgen/output/muxed/`
- Scene code + logs: `manimgen/output/scenes/`, `manimgen/output/logs/`

---

## LLM provider toggle

Resolution order (first wins):
1. `LLM_PROVIDER` env var
2. `llm_provider` in `config.yaml`
3. Default: `"gemini"`

Model names and `max_tokens` are configured under `llm:` in `config.yaml` — never hardcoded.

| Provider | Model | SDK |
|---|---|---|
| `gemini` | `gemini-2.5-flash` | `google.genai` (NOT the deprecated `google.generativeai`) |
| `anthropic` | `claude-sonnet-4-6` | `anthropic` |

---

## Video quality systems (current)

### 1. Prompt architecture
- `planner/prompts/researcher_system.md`: Panel of Experts prompt — simulates professor, pedagogy expert, and explainer creator. Returns rich JSON brief with historical context, textbook vs intuition, multiple perspectives, misconceptions, visual opportunities.
- `planner/prompts/planner_system.md`: outputs storyboard with `cues[{index, visual}]`. Each `visual` gives exact ManimGL-implementable descriptions.
- `generator/prompts/director_system.md`: ManimGL API reference, layout zone rules, banned patterns, technique table.
- `examples/`: hand-written verified ManimGL scenes. Each has a `techniques:` tag in its docstring — `scene_generator.py` reads this tag to select relevant examples per section automatically.

### 2. Dark background
All `manimgl` subprocess calls use `-c "#1C1C1C"`. The flag is `-c`, NOT `--background_color`.

### 3. Video quality
- Assembler uses `-preset slow -crf 17` (near-lossless). Previously `veryfast` caused blurry output.
- Output normalized to `1920x1080@60fps`.
- All FFmpeg/ffprobe subprocess calls have 300s timeouts.

### 4. codeguard.py auto-fixes (token-free)
Every fix runs before any render attempt. Key fixes:

| Pattern | Fix |
|---|---|
| `self.set_camera_orientation(phi=X, theta=Y)` | → `self.frame.reorient(Y, X)` |
| `reorient(theta_deg=..., phi_deg=...)` | → `reorient(theta_degrees=..., phi_degrees=...)` |
| `NumberLine(..., label=...)` | strip `label=` kwarg |
| `MathTex(...)` | → `Tex(...)` |
| `Create(...)` | → `ShowCreation(...)` |
| `x_length=` / `y_length=` in Axes | → `width=` / `height=` |
| `set_fill_color(...)` | → `set_fill(...)` |
| `self.play(obj.become(...))` | → `obj.become(...); self.play(ShowCreation(obj))` |
| `self.play(SurroundingRectangle(...))` | wrap in `ShowCreation()` |
| `Tex(r"\text{label}")` outer wrapper | strip to `Tex(r"label")` |
| `font_size=` on `Tex()` | left alone (valid param, handled internally) |
| negative/zero `self.wait()` | clamped to `0.01` |
| VGroup item assignment | banned pattern (not auto-fixable) |
| `FadeOut(a, b)` | → `FadeOut(a), FadeOut(b)` |

### 5. Retry loop
Order: codeguard auto-fix → error-aware fix (token-free) → LLM fix (budget-capped) → visual fix (if layout checker finds defects) → fallback.

After every fix, the file is **always reloaded from disk** — previously a bug caused the LLM fix to be silently discarded.

`SceneErrorType` enum classifies errors for targeted guidance: `PRECHECK_VGROUP`, `SYNTAX`, `IMPORT`, `ATTRIBUTE`, `TYPE`, `RUNTIME`.

### 6. Visual validation (two-tier)
- **Tier 1 (frame_checker.py):** zero-cost PIL-based — detects black frames, frozen frames, edge clipping.
- **Tier 2 (layout_checker.py):** LLM vision — multi-frame sampling at 25%/50%/75% of duration, returns structured `ISSUE | CAUSE | FIX` feedback that gets fed back into the retry loop.

### 7. TTS voice
- Engine: `edge-tts` (free, local)
- Voice: `en-US-AndrewMultilingualNeural`
- Speed: `+5%`
- Word timestamps via `WordBoundary` mode for CUE alignment

### 8. Muxer
- Video longer than audio → trim video to audio duration
- Audio longer than video → freeze last frame (`tpad`)
- Logs WARNING at diff > 1.0s, LARGE MISMATCH at diff > 1.5s

---

## Key rules for development

1. **Never use `--background_color`** — the correct flag is `-c "#1C1C1C"`
2. **`Tex()` accepts `font_size=`** — valid param, don't convert to `.scale()` (double-scales)
3. **All prompts live as `.md` files** in `prompts/` dirs — never inline Python strings
4. **Edit `manimgen/manimgen/`** — that's the importable package, not the top-level `manimgen/`
5. **codeguard is the first line of defense** — extend it for any new known-bad pattern before touching prompts
6. **No duplicate source files** — never create top-level mirrors of source files
7. **Adding a new example scene:** add to `examples/`, add `techniques: <name>` as first docstring line. No code changes needed.
8. **Master Guidelines (`MASTER GUIDELINES.md`)** — no hardcoded mappings, no duplicate sources of truth, no speculative abstractions.

---

## ManimGL API gotchas

### Camera orientation — the most common crash
```python
# WRONG — ManimCommunity, does not exist in ManimGL
self.set_camera_orientation(phi=60 * DEGREES, theta=-45 * DEGREES)

# RIGHT — ManimGL, ThreeDScene only
self.frame.reorient(theta_degrees, phi_degrees)   # positional, in degrees (no * DEGREES)
self.frame.reorient(-45, 60)                      # theta=-45°, phi=60°

# Wrong kwarg names (also caught by codeguard)
self.frame.reorient(theta_deg=-45, phi_deg=60)    # WRONG kwarg names
self.frame.reorient(theta_degrees=-45, phi_degrees=60)  # correct

# For 2D scenes: never use 3D camera calls at all
```

### NumberLine — no label= kwarg
```python
# WRONG — crashes with TypeError
NumberLine(x_range=[-3, 3], label="x")
# RIGHT — add label as a separate Text mobject
ax = NumberLine(x_range=[-3, 3])
label = Text("x").next_to(ax, RIGHT)
```

### Axes sizing
```python
# ManimGL uses width= and height= (NOT x_length/y_length — that's ManimCommunity)
axes = Axes(
    x_range=[-2, 3, 1], y_range=[-1, 5, 1],
    width=7, height=4.5,
    axis_config={"color": GREY_B, "include_tip": True},
    x_axis_config={"include_numbers": True, "decimal_number_config": {"font_size": 24}},
).center().shift(DOWN * 0.8)  # always shift down when title present
```

### self.frame — complete valid API (ThreeDScene only)
```python
self.frame.reorient(theta_degrees, phi_degrees)            # positional, degrees
self.frame.animate.reorient(theta_degrees, phi_degrees)    # animated
self.frame.add_updater(lambda m, dt: m.increment_theta(x)) # continuous orbit
self.frame.add_ambient_rotation(angular_speed=0.2)         # auto-spin (kwarg is angular_speed, NOT speed)
self.frame.clear_updaters()                                # stop rotation
self.frame.animate.scale(factor)
self.frame.animate.move_to(point)
```

**Does NOT exist:**
- `self.set_camera_orientation(...)` — ManimCommunity only
- `self.frame.set_euler_angles(...)` — use `reorient()`
- `add_fixed_in_frame_mobjects(label)` — use `label.fix_in_frame()`
- `add_ambient_rotation(speed=...)` — kwarg is `angular_speed=`

### 3D surfaces
```python
# ParametricSurface — uv_func returns raw 3D np.array, NOT axes.c2p()
surface = ParametricSurface(
    lambda u, v: np.array([u, v, np.sin(u) * np.cos(v)]),
    u_range=(-PI, PI), v_range=(-PI, PI), resolution=(32, 32),
)

# set_color_by_xyz_func takes a GLSL string, NOT a Python lambda
surface.set_color_by_xyz_func("z", min_value=-1.0, max_value=1.0, colormap="viridis")

# set_shading(reflectiveness, gloss, shadow)
surface.set_shading(0.8, 0.5, 0.5)

# Pin 2D text to screen in ThreeDScene
label.fix_in_frame()   # correct — add_fixed_in_frame_mobjects() does NOT exist
```

### Multiple annotations on same anchor
```python
# WRONG — labels stack on top of each other
label1.next_to(anchor, DOWN)
label2.next_to(anchor, DOWN)

# RIGHT — group and arrange
VGroup(label1, label2).arrange(DOWN, buff=0.4).next_to(anchor, DOWN)
```

---

## Fixed bugs log

### set_camera_orientation crash — FIXED 2026-04-10 (branch antigravity)
The Director generates `self.set_camera_orientation(phi=60*DEGREES, theta=-45*DEGREES)` (ManimCommunity API) on 2D scenes, causing `AttributeError` on every render attempt. Also generated `self.frame.reorient(theta_deg=..., phi_deg=...)` with wrong kwarg names. Also generated `NumberLine(..., label=...)` which crashes with TypeError.

**Fix (codeguard.py):**
- `_fix_set_camera_orientation()` — auto-rewrites to `self.frame.reorient(theta, phi)`, or `pass` if unparseable
- `_fix_reorient_wrong_kwargs()` — renames `theta_deg=` → `theta_degrees=`, `phi_deg=` → `phi_degrees=`
- `_strip_label_kwarg_from_numberline()` — strips `label=` from `NumberLine()`
- Banned patterns added for all three

**Director prompt:** added `set_camera_orientation` to the "Never use" table. Fixed `reorient()` docs to show correct param names.

### Stale render cache path mismatch — FIXED (branch antigravity)
`_render_is_fresh()` was called with a hardcoded path that didn't match actual ManimGL output. Fixed: now uses `_find_rendered_video(class_name)` to locate the actual video before checking freshness.

### Retry loop silent discard — FIXED (branch antigravity)
After LLM fix was applied, code was only reloaded from disk if codeguard had something to patch. LLM fix was silently discarded otherwise. Now always reloads after every fix attempt.

### A/V sync bugs — FIXED (earlier branches)
1. Cue 0 silence — segmenter measures from 0.0
2. Last-syllable clipping — uses `word.end` not `word.start` of next
3. MP3 stream copy drift — re-encodes to AAC
4. tpad wrong duration — uses gap not total audio
5. Sample rate drift — enforces `-ar 48000`

### Cue index off-by-one — FIXED
LLM now instructed that `cues[]` needs N+1 entries (N [CUE] markers + opening segment at index 0).

### VGroup swap pattern — FIXED
`_fix_become_inside_play()`, `set_fill_color` → `set_fill`, VGroup assignment banned.

### Video quality (blurry output) — FIXED (branch antigravity)
Assembler changed from `-preset veryfast` to `-preset slow -crf 17`.

---

## Known issues / next steps (as of 2026-04-10)

### 1. HIGH — A/V timing mismatch (freeze-frame tails)
Most visible quality issue. Cues often have 1–8 seconds of frozen still at the end because the Director miscalculates total animation time when animations are inside loops.

**Root cause:** Director subtracts one iteration's `run_time` instead of `n * run_time`.

**Fix plan:**
- Director prompt: make the loop timing rule impossible to miss (already has example, but LLM ignores it under pressure — move to top of Cue timing section)
- `timing_verifier.py` is already built — wire it into the retry loop as a pre-render check

**Files:** `generator/prompts/director_system.md`, `validator/timing_verifier.py`, `validator/retry.py`

### 2. MEDIUM — timing_verifier.py not wired in
Built and tested but not called anywhere in the pipeline. Should run after codeguard, before rendering, to catch timing mismatches at zero cost.

**Files:** `validator/retry.py` (add call after `precheck_and_autofix`)

### 3. MEDIUM — frame_checker.py wiring unclear
Built with tests but unclear if it's actually called in the retry loop.

### 4. LOW — `.hypothesis/` and `.DS_Store` committed
Should be in `.gitignore`. Clutters diffs.

### 5. LOW — `_load_llm_config()` called on every LLM call
Parses `config.yaml` twice per `chat()` call. Should be cached at module load.

### 6. LOW — Cue-word tokenization mismatch risk
`cue_parser` uses `str.split()` word counts; edge-tts may tokenize differently.

### 7. LOW — PDF rendering unconditional
Renders every page to PNG even for text-heavy PDFs. Should use 3-way logic.

---

## Testing (zero cost)

```bash
# Full suite (skip LLM-calling tests)
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q

# Specific areas
python3 -m pytest tests/test_codeguard.py -v          # all codeguard fixes + bans
python3 -m pytest tests/test_pipeline_contracts.py -v  # A/V sync contracts
python3 -m pytest tests/test_research_step.py -v       # researcher + planner
python3 -m pytest tests/test_frame_checker.py -v       # zero-cost visual checks
python3 -m pytest tests/test_timing_verifier.py -v     # static timing analysis
```

---

## A/V sync — fixed contracts (test_pipeline_contracts.py)

1. Cue 0 measured from `0.0` — pre-speech silence preserved
2. Segment boundary uses `word.end` — last syllable never clipped
3. Slicer re-encodes to AAC — no ~26ms MP3 frame-boundary drift
4. Muxer `tpad` uses gap — not total audio duration
5. Assembler enforces `-ar 48000` in all three re-encode paths
