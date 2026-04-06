# ManimGen — Session Guide

## What this project is
Automated pipeline: **topic string or PDF → 3Blue1Brown-style animated explainer video with narration.**
Uses an audio-first CUE pipeline where spoken word timestamps drive animation durations — no speed warping.

**Stack:** Python 3.13, ManimGL (3b1b fork), Gemini 2.5 Flash, FFmpeg 8.1, LaTeX, edge-tts, Flask (editor)

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

## Pipeline flow (audio-first CUE architecture)

```
topic / PDF
  → parse_input() / pdf_parser
  → plan_lesson()              LLM → plan JSON with narration containing [CUE] markers
  → cue_parser.parse_cues()    strips [CUE] → clean narration + cue_word_indices
  → plan saved to manimgen/output/plan.json

For each section:
  1. TTS (edge-tts WordBoundary)  → full .mp3 + per-word timestamps (sub-ms)
  2. segmenter.compute_segments() → exact duration per cue from word onset times
  3. audio_slicer.slice_audio()   → section_01_cue00.mp3, _cue01.mp3, ...
  4. For each cue segment:
     a. generate_scenes(duration_seconds=seg.duration)  → ManimGL .py
     b. run_scene() → manimgl renders silent .mp4
     c. retry_scene() on failure (codeguard → error-aware → LLM fix)
     d. fallback_scene() if all retries fail
     e. mux_audio_video() → pad-only, never warp
  5. section_videos collected

→ assemble_video()  normalize + xfade transitions → final .mp4
→ manimgen-edit     browser UI for clip reorder/trim/export
```

**Key insight:** Durations flow FROM spoken audio INTO scene generation. Animation length matches narration because the scene generator receives the exact `duration_seconds` from the segmenter. Nothing is ever speed-warped.

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

### 1. Prompt architecture
- `generator_system.md`: ~200 lines, aesthetic-first structure. Identity + spatial layout at top (LLM attention priority). Compact API cheatsheet, no bloated reference.
- `rules_core.md`: shared by generator and retry prompts. Banned kwargs, color mappings, arrow reference, duration rules.
- 9-rule LAYOUT RULES checklist injected into every user message in `scene_generator.py`.

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

### Axes sizing
Always: `Axes(...).set_width(10).center()` — with title: `.shift(DOWN * 0.5)`

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
