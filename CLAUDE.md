# ManimGen — Session Guide

## What this project is
Automated pipeline: **topic string or PDF → 3Blue1Brown-style animated explainer video with narration.**
Uses an audio-first CUE pipeline where spoken word timestamps drive animation durations — no speed warping.

**Stack:** Python 3.13, ManimGL (3b1b fork), Gemini 2.5 Flash, FFmpeg 8.1, LaTeX, edge-tts, Flask (editor)

**Current branch: `feature/director-overhaul`** — architectural overhaul complete. Template engine deleted. See pipeline section below.

**Repo:** `https://github.com/Varkot-dev/videomaking.git` — branch `fix/camera-framing`
**229 tests, all passing** (`python3 -m pytest tests/ -v`)

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
│   ├── input/
│   │   ├── parser.py            # normalize topic string
│   │   └── pdf_parser.py        # extract text + render pages from PDF
│   ├── planner/
│   │   ├── lesson_planner.py    # LLM → lesson plan JSON (with _safe_json_loads)
│   │   ├── cue_parser.py        # parse [CUE] markers → cue_word_indices
│   │   ├── segmenter.py         # word timestamps + cue indices → CueSegment durations
│   │   └── prompts/             # planner_system.md, planner_pdf_system.md
│   ├── generator/
│   │   ├── scene_generator.py   # LLM → ManimGL scene code (per-cue, exact duration)
│   │   └── prompts/             # generator_system.md, rules_core.md
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
│   │   ├── audio_slicer.py      # full audio → N cue-aligned .mp3 slices
│   │   ├── muxer.py             # audio+video mux (pad-only, no speed warp)
│   │   └── assembler.py         # normalize 1920x1080@30fps, xfade between sections
│   └── editor/
│       ├── server.py            # Flask UI for clip review, reorder, trim, export
│       └── templates/editor.html
├── examples/                    # hand-written verified ManimGL scenes
├── tests/                       # 229 pytest tests
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

## Running the pipeline

```bash
# From manimgen/ project root
export GEMINI_API_KEY=...       # or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic

manimgen "binary search"        # topic mode (3-8 sections)
manimgen --pdf notes.pdf        # PDF mode (6-10 sections)
manimgen --resume               # resume from cached plan.json (skips already-muxed)

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
- `planner/prompts/planner_system.md`: outputs storyboard with `cues[{index, visual}]` — pixel-level visual descriptions per cue. NOT concept descriptions.
- `generator/prompts/director_system.md`: full ManimGL API reference, layout zone rules with exact coordinates, banned patterns, examples. The Director writes complete Python.
- `examples/`: 10+ hand-written verified ManimGL scenes covering diverse patterns — the Director's few-shot reference library.
- **DELETED:** `generator/prompts/spec_system.md`, `templates/` directory, `spec_schema.py` — the template engine is gone.

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
2. **ManimGL `Tex()` does NOT accept `font_size=`** — only `Text()` does. codeguard converts it to `.scale()`.
3. **All prompts live as `.md` files** in `prompts/` dirs — never inline Python strings
4. **Retry order:** codeguard auto-fix → error-aware fix (token-free) → LLM fix (budget-capped) → fallback
5. **Token budget:** `MAX_LLM_FIX_CALLS` (default 1) limits paid retry calls
6. **JSON from LLMs can have bad escapes** — `_safe_json_loads` in `lesson_planner.py` handles `\e`, `\s`, etc.
7. **Section caps:** 8 (topic) / 10 (PDF), enforced by `_cap_sections()`
8. **Plan caching:** `--resume` flag skips LLM planning call, reads `manimgen/output/plan.json`
9. **Log everything:** every attempt's code and stderr goes to `manimgen/output/logs/`
10. **codeguard is the first line of defense** — extend it for any new known-bad pattern before touching prompts

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

---

## Known issues / next steps

1. **Cue-word tokenization mismatch risk:** `cue_parser` uses `str.split()` word counts; edge-tts may tokenize differently. No integration test guards this yet.
2. **config.yaml partially wired:** `tts.*` and `llm_provider` are read by code. `output.*` and `rendering.*` paths are still hardcoded.
3. **PDF rendering:** Currently renders every page to PNG unconditionally, even text-heavy PDFs. Should use 3-way logic (text-only / image-only / mixed).
4. **No single integration test** for the full plan → TTS → slice → render → mux → assemble pipeline (only unit tests per module).
5. **layout_checker.py uses LLM vision** — costs money per rendered frame check. Consider making it opt-in or running only on non-fallback scenes.

---

## Recurring bugs and architectural problems (DO NOT FORGET THESE)

These are real issues raised by the user that keep getting patched incorrectly or ignored. Read this before starting any work.

### BUG 1: Axes overflow into title zone
**Symptom:** The curve/axes graphic extends into the title text at the top of the frame. The graph line literally overlaps the title words.
**Root cause:** Templates do `set_width(N)` which preserves the axes' native aspect ratio. When y_range span >> x_range span, the axes become taller than the safe content zone. With a title present, the content zone is roughly y∈[-3, 2.5] in ManimGL coords, but a tall axes with `.shift(DOWN * 1.2)` still extends above y=2.5 into the title.
**Correct fix:** After `set_width()`, check `axes.get_height()` and clamp it: `if axes.get_height() > 4.5: axes.set_height(4.5)`. This is NOT a hardcode — it's a geometric constraint. Apply this in ALL templates that use axes with a title, not just limit_template.
**Status:** Partially fixed in `limit_template.py` and `function_template.py` — verify other axis-using templates (number_line, complex_plane, surface_3d, etc.) also have this clamp.

### BUG 2: Every scene looks identical — templates are too rigid
**Symptom:** All generated clips look the same: title → axes → curve → dot moves → annotation. Different sections with genuinely different visual concepts produce indistinguishable output.
**Root cause:** The `limit` template (and others) have a fixed, narrow set of beat types. The `limit` template only knows: axes_appear, curve_appear (always renders a hole), guide_lines, approach_dot, annotation. It cannot render:
- A jump discontinuity (two separate curves at different y-levels, no hole)
- Epsilon-delta bands (two colored rectangles shrinking around a point)
- Oscillation (sin(1/x) style, no single limit)
- A piecewise function with a separate filled point off the curve
When the planner asks for these, the template shoe-horns them into the hole pattern → same visual every time.
**Correct fix:** Add beat types to each template for the actual visual variations that concept requires. For `limit`: add `jump_discontinuity`, `epsilon_delta`, `separate_point` beat types. Don't just add more fields to existing beats.

### BUG 3: Each cue clip restarts from scratch
**Symptom:** A section with 4 cues produces 4 clips that each independently do: title appears → axes appear → curve appears → dot moves. The viewer sees the same setup repeated 4 times.
**Root cause:** Each cue is an independent `Scene` class with its own `construct()`. There is no state carried between cues. The assembler hard-cuts between cues within a section, so visually it looks like 4 restarts.
**Correct fix (architectural):** Either (a) make cues within a section share a single scene file with `self.wait()` pauses between cue points, or (b) the first cue builds the axes/curve and subsequent cues receive them as already-present objects that just get animated further. This requires rethinking how `generate_scenes` works for cue_index > 0 — it should know what was already rendered and only animate the delta.

### BUG 4: `retry_spec()` in `retry.py` called `_load_spec_system_prompt()` which doesn't exist there
**What happened:** `retry_spec()` needs the spec system prompt (the one that knows about templates and beat types) but called a function that only exists in `scene_generator.py`. Fixed by loading the prompt file directly via relative path.
**Lesson:** When spec retry fails, it must use the SPEC system prompt (generator/prompts/spec_system.md), not the CODE retry prompt (validator/prompts/retry_system.md). The code retry prompt knows about ManimGL Python. The spec prompt knows about JSON templates. They are completely different.

### BUG 5: `SpecValidationError` crashes the pipeline instead of falling back
**What happened:** When all spec retries are exhausted, `generate_scenes()` raises `SpecValidationError`. This was uncaught in `cli.py`, crashing the entire run instead of gracefully using `fallback_scene()`.
**Fix:** Wrap `generate_scenes()` call in `cli.py` with `except SpecValidationError` → set `success = False`, let the existing fallback logic handle it.

### ARCHITECTURAL ISSUE: Template system vs. free-form codegen trade-off
The template engine was built to eliminate ManimGL API hallucination bugs. It succeeded at that. But it introduced a new problem: templates are so rigid that every section looks the same. The original free-form codegen produced varied visuals but broke constantly.
**The right answer is NOT to choose one or the other.** It is:
1. Templates handle the structure and API correctness (axes, coordinate systems, basic shapes)
2. The LLM fills in the parameters (which curve, which colors, which values)
3. Templates need more beat types to cover the real visual space of each concept
Do NOT regress to pure free-form codegen. Do NOT make templates so rigid they produce identical output. The fix is richer templates with more beat types.

---

## Testing (zero cost)

```bash
python3 -m pytest tests/ -v                    # all 229 tests
python3 -m pytest tests/test_codeguard.py -v   # just static fixes
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
