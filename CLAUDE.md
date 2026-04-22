# ManimGen — Session Guide

## What this is
Automated pipeline: **topic/PDF → 3Blue1Brown-style animated explainer video.**
Audio-first CUE pipeline: spoken word timestamps drive animation durations — no speed warping.

**Stack:** Python 3.13, ManimGL (3b1b fork), Gemini 2.5 Flash, FFmpeg 8.1, LaTeX, edge-tts, Flask
**Repo:** `https://github.com/Varkot-dev/videomaking.git` — active branch: `chore/flatten-manimgen-nesting`
**Tests:** `python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q`

---

## Session start
- Spawn a **Haiku subagent** for any 3+ file analysis; return summarized insights only
- Pipe all long shell output through `| head -N` — never dump full output
- Use `offset`/`limit` on Read; grep first to find the exact lines needed
- Quick fixes: make immediately, no discussion

---

## Critical: ManimGL ≠ ManimCommunity

| Use (ManimGL) | Never use |
|---|---|
| `from manimlib import *` | `from manim import *` |
| `ShowCreation(obj)` | `Create(obj)` |
| `Tex(...)` | `MathTex(...)` |
| `self.frame.reorient(theta, phi)` | `self.set_camera_orientation(...)` |
| `width=` / `height=` in Axes | `x_length=` / `y_length=` |
| `-c "#1C1C1C"` | `--background_color` |
| `label.fix_in_frame()` | `add_fixed_in_frame_mobjects(label)` |
| `add_ambient_rotation(angular_speed=0.2)` | `add_ambient_rotation(speed=...)` |

`codeguard.py` auto-fixes most of the above before any render attempt.

---

## Source layout

```
manimgen/manimgen/   ← importable package — always edit here, never create top-level mirrors
├── cli.py           # entry point
├── llm.py           # Gemini/Anthropic toggle (config-driven)
├── input/           # parser.py, pdf_parser.py
├── planner/         # lesson_planner.py, cue_parser.py, segmenter.py, prompts/
├── generator/       # scene_generator.py, prompts/director_system.md
├── validator/       # codeguard.py, runner.py, retry.py, fallback.py, layout_checker.py, frame_checker.py
├── renderer/        # tts.py, audio_slicer.py, cutter.py, muxer.py, assembler.py
└── editor/          # server.py (Flask clip review UI)
examples/            # hand-written ManimGL scenes; first docstring line must be `techniques: <name>`
config.yaml          # LLM provider, model names, TTS, render quality
```

---

## Pipeline flow

```
topic/PDF → parse → research_topic() → plan_lesson() → plan.json

Per section:
  TTS → word timestamps → segmenter → audio slices
  → Director (1 LLM call) → 1 Scene class → codeguard → render
  → retry (codeguard → error-fix → LLM fix → visual fix → fallback)
  → cutter → per-cue clips → muxer → assembled final.mp4
```

**Key decisions:**
- One scene per section — axes build once, cues animate on top; no restarts
- Director writes ManimGL Python directly — no template engine
- After every fix, code always reloads from disk (codeguard patches in-place)
- Render cache: `.hash` sidecar detects stale renders automatically

---

## Commands

```bash
# Pipeline
GEMINI_API_KEY=<key> manimgen "gradient descent"
GEMINI_API_KEY=<key> manimgen --pdf notes.pdf
GEMINI_API_KEY=<key> manimgen --resume        # resume from cached plan.json
manimgen-edit                                  # clip editor UI

# Tests (zero cost)
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
python3 -m pytest tests/test_codeguard.py -v
python3 -m pytest tests/test_pipeline_contracts.py -v

# Output: output/videos/<title>.mp4 | output/muxed/ | output/scenes/ | output/logs/
```

---

## LLM provider

Resolution: `LLM_PROVIDER` env var → `config.yaml llm_provider` → default `gemini`

| Provider | Model | SDK |
|---|---|---|
| `gemini` | `gemini-2.5-flash` | `google.genai` (NOT `google.generativeai`) |
| `anthropic` | `claude-sonnet-4-6` | `anthropic` |

---

## Key rules

1. `Tex()` accepts `font_size=` — don't convert to `.scale()` (double-scales)
2. `reorient(theta, phi)` positional, degrees — no `* DEGREES`, no `theta_deg=` kwarg
3. All prompts are `.md` files in `prompts/` dirs — never inline Python strings
4. Extend `codeguard` for new bad patterns before touching prompts
5. `examples/` scenes need `techniques: <name>` as first docstring line — no code changes needed
6. If not evaluating LLM output quality, hardcode the scene — faster, free, exact

---

## Known issues (as of 2026-04-10)

| Priority | Issue | Files |
|---|---|---|
| HIGH | A/V freeze-frame tails — Director miscalculates loop timing | `director_system.md`, `timing_verifier.py`, `retry.py` |
| MEDIUM | `timing_verifier.py` built but not wired into retry loop | `validator/retry.py` |
| MEDIUM | `frame_checker.py` wiring unclear | `validator/retry.py` |
| LOW | `.hypothesis/` and `.DS_Store` committed | `.gitignore` |
| LOW | `_load_llm_config()` parses `config.yaml` twice per call — cache at module load | `llm.py` |
| LOW | Cue-word tokenization mismatch risk (split() vs edge-tts) | `cue_parser.py` |
| LOW | PDF page render unconditional — should use 3-way logic | `pdf_parser.py` |

---

## A/V sync contracts (test_pipeline_contracts.py)

1. Cue 0 from `0.0` — pre-speech silence preserved
2. Segment boundary uses `word.end` — last syllable never clipped
3. Slicer re-encodes to AAC — no ~26ms MP3 drift
4. Muxer `tpad` uses gap — not total audio duration
5. Assembler enforces `-ar 48000` in all three re-encode paths
