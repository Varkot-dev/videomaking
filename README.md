# ManimGen

An automated pipeline that converts a topic string or PDF of lecture notes into a narrated, animated CS explainer video in the style of 3Blue1Brown.

**Input:** `"binary search"` or `lecture.pdf`  
**Output:** A rendered `.mp4` with voiceover, 5–10 minutes of animated content

---

## How it works

The core challenge is that generating correct [ManimGL](https://github.com/3b1b/manim) animation code is hard — ManimGL has a narrow, finicky API that differs significantly from its community fork, and LLMs consistently produce code that crashes on the first attempt. This project's main engineering contribution is a multi-stage validation and repair harness that gets generated code to render reliably without human intervention.

### Pipeline

```
Input (topic string or PDF)
        │
        ▼
┌─────────────────────┐
│  Researcher         │  LLM → structured knowledge brief
│                     │  (Panel of Experts: professor, pedagogy expert, explainer)
└──────────┬──────────┘
           │
           ▼
┌─────────────────────┐
│  Lesson Planner     │  LLM → storyboard JSON with:
│                     │   - narration with [CUE] markers
│                     │   - cues[]: [{index, visual}] per cue
└──────────┬──────────┘
           │  up to 8 sections (topic) / 10 sections (PDF)
           ▼
┌────────────────────────────────────────────────────────────┐
│  Global Audio Phase (runs BEFORE any codegen)              │
│                                                            │
│  For each section:                                         │
│  TTS (edge-tts WordBoundary) → .mp3 + per-word timestamps  │
│  segmenter.compute_segments() → exact cue durations        │
│  audio_slicer → N cue-aligned .m4a clips                   │
└──────────┬─────────────────────────────────────────────────┘
           │
           ▼
┌────────────────────────────────────────────────────────────┐
│  For each section:                                         │
│                                                            │
│  1. Director ────────────────► ONE LLM call                │
│     (scene_generator.py)        storyboard + cue durations │
│                                 → single ManimGL Scene     │
│                                                            │
│  2. Codeguard ───────────────► AST + regex static fixes    │
│     (token-free)                50+ known ManimGL API      │
│                                 mistakes auto-corrected    │
│                                                            │
│  3. AST gate ────────────────► scene_ast_gate: only allows │
│     (security, token-free)      whitelisted top-level      │
│                                 statements (no shell-out)  │
│                                                            │
│  4. Timing verifier ─────────► static loop-aware timing    │
│     (token-free)                analysis; auto-fix or      │
│                                 route to retry             │
│                                                            │
│  5. Runner ──────────────────► subprocess: manimgl file.py │
│                                 1920×1080 @ 60fps, H.264   │
│                                                            │
│  6. Render validator ────────► frame_checker (PIL, free)   │
│     (two-tier)                  + layout_checker (LLM      │
│                                 vision, on failure only)   │
│                                                            │
│  7. Retry loop ──────────────► classify error → targeted   │
│     (up to 3×)                  LLM fix → codeguard →      │
│                                 re-render; fallback scene  │
│                                 if all retries exhausted   │
│                                                            │
│  8. Cutter ──────────────────► cut section .mp4 into       │
│                                 N per-cue clips (FFmpeg)   │
│                                                            │
│  9. Muxer ───────────────────► overlay narration audio per │
│                                 cue clip; pad-only, never  │
│                                 speed-warp                 │
└──────────┬─────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────┐
│  Assembler          │  normalize + xfade → final .mp4
└─────────────────────┘
```

---

## The hard part: reliable code generation for ManimGL

ManimGL's API is not well-represented in LLM training data and has diverged significantly from ManimCommunity (the fork most tutorials cover). Raw LLM output fails on the first attempt with errors like:

- Wrong import (`from manim import *` vs `from manimlib import *`)
- Nonexistent methods (`Create`, `MathTex`, `Circumscribe`)
- Invalid kwargs (`tip_length`, `corner_radius`, `scale_factor` on FadeIn)
- Wrong color names (`DARK_GREY`, `DARK_BLUE` don't exist — use `GREY_D`, `BLUE_D`)
- Zero-length Arrow construction (divide-by-zero crash)
- Loop timing errors — subtracting one iteration's `run_time` instead of all n iterations, leaving multi-second freeze-frame tails

### Codeguard (`validator/codeguard.py`)

Before any render attempt, a token-free static analysis pass runs AST rewrites and regex replacements to fix known-bad patterns deterministically. Key auto-fixes:

```python
"from manim import *"               → "from manimlib import *"
"MathTex(r'x^2')"                   → "Tex(r'x^2')"
"Create(circle)"                    → "ShowCreation(circle)"
"FadeIn(obj, scale_factor=1.5)"     → "FadeIn(obj)"
"DARK_GREY"                         → "GREY_D"
"color_gradient([A, B], n)"         → "color_gradient([A, B], int(n))"
Arrow(ORIGIN, ORIGIN)               → Arrow(ORIGIN, DOWN * 0.5)
set_camera_orientation(phi, theta)  → self.frame.reorient(theta, phi)
x_length= / y_length= in Axes      → width= / height=
negative self.wait()                → self.wait(0.01)
```

Eliminates the majority of failures without spending tokens. Only errors codeguard can't fix deterministically reach the LLM.

### Timing verifier (`validator/timing_verifier.py`)

Statically analyses each generated scene before rendering to compute animation time per cue. Detects loop timing bugs and auto-corrects `self.wait()` values to fill the cue's exact narration duration. Runs zero-cost before every render attempt — closing the feedback loop that previously only triggered at mux time (after a 30–120 s render).

### Error-aware retry (`validator/retry.py`)

When codeguard can't fix the code:
1. Classifies error type from stderr (`syntax`, `import`, `attribute`, `type`, `runtime`)
2. Generates targeted fix guidance for that error class
3. Sends `original_code + error + guidance` to the LLM for a targeted fix
4. Runs codeguard + timing verifier on result, then re-renders
5. Repeats up to 3× with a configurable LLM call budget (`MANIMGEN_MAX_RETRY_LLM_CALLS`)

---

## Audio-first CUE architecture

Narration audio drives animation timing — not the other way around.

1. TTS runs for all sections **before** any code is generated
2. `edge-tts WordBoundary` events give per-word timestamps at sub-millisecond precision
3. `segmenter.compute_segments()` converts word timestamps + `[CUE]` marker indices into exact per-cue durations
4. The Director receives these durations as hard constraints and writes `self.wait()` calls to match them
5. `muxer.py` pads (never speed-warps) — small mismatches from stream alignment are absorbed silently

---

## Project structure

```
manimgen/
├── manimgen/                     # source package
│   ├── cli.py                    # entry: manimgen <topic> | --pdf <file> | --resume
│   ├── llm.py                    # shared LLM client (Gemini / Anthropic / Ollama toggle)
│   ├── input/
│   │   ├── parser.py             # normalize topic string
│   │   └── pdf_parser.py         # PDF → cleaned text chunks (heading-based segmentation)
│   ├── planner/
│   │   ├── lesson_planner.py     # research_topic() + plan_lesson() → storyboard JSON
│   │   ├── cue_parser.py         # parse [CUE] markers → cue_word_indices
│   │   ├── segmenter.py          # word timestamps + cue indices → CueSegment durations
│   │   └── prompts/              # planner_system.md, planner_pdf_system.md, researcher_system.md
│   ├── generator/
│   │   ├── scene_generator.py    # Director: LLM → one ManimGL Scene per section
│   │   └── prompts/              # director_system.md
│   ├── validator/
│   │   ├── codeguard.py          # static analysis + 50+ auto-fixes
│   │   ├── manimlib_signatures.py# type-aware kwarg introspection (Phase 2 shadow)
│   │   ├── manimlib_symbols.py   # call-target name validation
│   │   ├── scene_ast_gate.py     # security: AST allowlist for top-level statements
│   │   ├── timing_verifier.py    # loop-aware cue timing analysis + auto-fix
│   │   ├── render_validator.py   # unified post-render quality gate (frame + layout)
│   │   ├── frame_checker.py      # zero-cost PIL: black/frozen/clipping detection
│   │   ├── layout_checker.py     # LLM vision: overlap/overflow/layout defect detection
│   │   ├── runner.py             # manimgl subprocess with -c #1C1C1C flag
│   │   ├── retry.py              # retry loop: codeguard → timing → error fix → LLM fix
│   │   ├── fallback.py           # styled bullet-point fallback scene (with TTS)
│   │   └── env.py                # render environment vars (LaTeX PATH)
│   ├── renderer/
│   │   ├── tts.py                # edge-tts with WordBoundary → per-word timestamps
│   │   ├── audio_slicer.py       # full audio → N cue-aligned .m4a slices (AAC)
│   │   ├── cutter.py             # cut rendered section .mp4 into per-cue clips
│   │   ├── muxer.py              # audio+video mux (pad-only, no speed warp)
│   │   └── assembler.py          # normalize 1920x1080@60fps, xfade transitions
│   └── editor/
│       ├── server.py             # Flask clip editor server
│       └── templates/editor.html # browser-based trim/reorder/export UI
├── examples/                     # hand-written verified ManimGL scenes (Director few-shot)
│                                 # Each has `techniques: <name>` in class docstring
├── tests/                        # 732+ unit tests, zero LLM or subprocess calls
├── docs/
│   └── KNOWN_ISSUES.md           # active failure log + env-doctor guards
├── scripts/
│   └── env_doctor.py             # session-start health checks (editable install, deps, etc.)
├── config.yaml                   # LLM provider, model names, TTS config, render quality
├── requirements.txt
└── setup.py                      # console_scripts: manimgen, manimgen-edit
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Animation engine | [ManimGL](https://github.com/3b1b/manim) (3b1b version, not ManimCommunity) |
| LLM — development | Google Gemini 2.5 Flash |
| LLM — production | Anthropic Claude Sonnet |
| TTS | Microsoft edge-tts (Neural voices, WordBoundary timestamps) |
| Video processing | FFmpeg |
| PDF parsing | pypdf |
| Clip editor | Flask + vanilla JS |
| Tests | pytest (732+ tests, fully mocked) |
| Output format | H.264, 1920×1080, 60fps |

---

## Setup

```bash
git clone https://github.com/Varkot-dev/videomaking.git
cd videomaking

pip install -e manimgen/
pip install pypdf edge-tts google-genai anthropic pyyaml flask pillow

# Set your LLM provider
export GEMINI_API_KEY=your_key        # development (default)
# export ANTHROPIC_API_KEY=your_key  # production
# export LLM_PROVIDER=anthropic
```

**Dependencies:** FFmpeg and BasicTeX (for LaTeX rendering in ManimGL)
```bash
brew install ffmpeg
brew install --cask basictex
pip install 'manimgl==1.7.2'
pip install 'setuptools<81'   # manimgl 1.7.2 requires pkg_resources
```

Run the environment health check before your first pipeline run:
```bash
python3 manimgen/scripts/env_doctor.py
```

---

## Usage

```bash
# Topic mode — cheapest, best for testing
manimgen "binary search"
manimgen "gradient descent"
manimgen "dynamic programming"

# PDF mode — from lecture notes or papers
manimgen --pdf lecture.pdf

# Resume a previous run from cached plan
manimgen --resume

# Cap LLM retry calls (reduces cost during testing)
export MANIMGEN_MAX_RETRY_LLM_CALLS=0   # deterministic fixes only, no LLM retries

# Enable Phase 3 kwarg enforcement (strips provably-invalid kwargs before render)
export MANIMGEN_KWARG_ENFORCE=1

# Adjust freeze-frame detection threshold (default: 2.5s)
export MANIMGEN_FREEZE_BLOCK_THRESHOLD=2.0

# Edit rendered clips before final export
manimgen-edit                           # auto-loads muxed/ or videos/
manimgen-edit --videos path/to/clips/
```

Output: `manimgen/output/videos/<title>.mp4`

---

## Testing

```bash
# Full suite (skip LLM-calling tests — zero cost, zero subprocess calls)
python3 -m pytest manimgen/tests/ \
  --ignore=manimgen/tests/test_scene_generator.py \
  --ignore=manimgen/tests/test_planner.py \
  --ignore=manimgen/tests/test_pipeline_e2e.py -q
```

732+ tests covering:
- Every codeguard auto-fix and banned pattern
- Type-aware kwarg introspection (manimlib_signatures)
- Loop-aware timing analysis and auto-fix (timing_verifier)
- AST security gate (scene_ast_gate)
- Error-aware repair from real stderr tracebacks
- Section cap enforcement in the planner
- A/V sync contracts (muxer, slicer, segmenter)
- Frame defect detection (frame_checker)
- PDF parser output structure and chunking logic

---

## Cost model

Each `manimgen` run makes approximately `2 + (N × 1.5)` LLM calls where N = number of sections:
- 1 call for research
- 1 call for lesson planning
- 1 call per section for scene generation
- ~0.5 calls/section average for retries (with `MAX_LLM_FIX_CALLS=1`)

At Gemini Flash pricing, a 5-section topic run costs ~$0.02–$0.05. A 10-section PDF run costs ~$0.05–$0.15.

Set `tts.enabled: false` in `config.yaml` to skip narration during development.
