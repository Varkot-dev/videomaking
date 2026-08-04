# ManimGen

An automated pipeline that converts a topic string or PDF of lecture notes into a narrated, animated CS explainer video in the style of 3Blue1Brown.

**Input:** `"binary search"` or `lecture.pdf`  
**Output:** A rendered `.mp4` with voiceover, 5–10 minutes of animated content

---

## How it works

The core challenge is that generating correct [ManimGL](https://github.com/3b1b/manim) animation code is hard — ManimGL has a narrow, finicky API that differs significantly from its community fork, and LLMs consistently produce code that crashes on the first attempt. This project's main engineering contribution is a multi-stage validation and repair pipeline that gets generated code to render reliably without human intervention.

### Pipeline

```
Input (topic string or PDF)
        │
        ▼
┌─────────────────┐
│  Lesson Planner │  LLM → structured JSON lesson plan
│                 │  (title, sections, narration scripts, visual descriptions)
└────────┬────────┘
         │  up to 8 sections (topic) / 10 sections (PDF)
         ▼
┌─────────────────────────────────────────────────────────┐
│  For each section:                                      │
│                                                         │
│  1. Scene Generator ──► LLM writes ManimGL Python       │
│                         (one Scene class per file)      │
│                                                         │
│  2. Codeguard ──────────► Static analysis + auto-fix    │
│     (token-free)          regex-based repair of 20+     │
│                           known ManimGL API mistakes    │
│                                                         │
│  3. Runner ─────────────► subprocess: manimgl file.py   │
│                           1920×1080 @ 60fps, H.264      │
│                                                         │
│  4. Retry loop ─────────► classify error type →         │
│     (up to 3×)            targeted LLM fix prompt →     │
│                           codeguard → re-render         │
│                                                         │
│  5. Fallback ───────────► title card scene if all       │
│                           retries fail                  │
│                                                         │
│  6. TTS ────────────────► edge-tts narration → .mp3     │
│                                                         │
│  7. Muxer ──────────────► ffmpeg: sync audio+video,     │
│                           pad to match durations         │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌─────────────────┐
│   Assembler     │  ffmpeg concat → final .mp4
└─────────────────┘
```

---

## The hard part: reliable code generation for ManimGL

ManimGL's API is not well-represented in LLM training data and has diverged significantly from ManimCommunity (the fork most tutorials cover). Raw LLM output fails ~60% of the time on first attempt with errors like:

- Wrong import (`from manim import *` vs `from manimlib import *`)
- Nonexistent methods (`Create`, `MathTex`, `Circumscribe`)
- Banned kwargs (`tip_length`, `corner_radius`, `scale_factor` on FadeIn)
- Wrong color names (`DARK_GREY`, `DARK_BLUE` don't exist — use `GREY_D`, `BLUE_D`)
- Zero-length Arrow construction (divide-by-zero crash)
- Mixed `.animate` + `FadeIn` in a single `self.play()` call (TypeError)
- `color_gradient()` receiving float instead of int

### Codeguard (`validator/codeguard.py`)

Before any LLM retry, a token-free static analysis pass runs regex replacements and AST validation to fix known-bad patterns deterministically:

```python
# auto-fix examples
"from manim import *"          → "from manimlib import *"
"MathTex(r'x^2')"              → "Tex(r'x^2')"
"Create(circle)"               → "ShowCreation(circle)"
"FadeIn(obj, scale_factor=1.5)"→ "FadeIn(obj)"
"DARK_GREY"                    → "GREY_D"
"color_gradient([A, B], n)"    → "color_gradient([A, B], int(n))"
Arrow(ORIGIN, ORIGIN)          → Arrow(ORIGIN, DOWN * 0.5)
```

This eliminates ~70% of failures without spending tokens. Only errors codeguard can't fix deterministically go to the LLM for repair.

### Error-aware retry (`validator/retry.py`)

When codeguard can't fix the code, the retry loop:
1. Classifies the error type from stderr (`syntax`, `import`, `attribute`, `type`, `runtime`)
2. Generates targeted fix guidance for that error class
3. Sends `original_code + error + guidance` to the LLM for a targeted fix
4. Runs codeguard on the result, then re-renders
5. Repeats up to 3× with a configurable LLM call budget (`MANIMGEN_MAX_RETRY_LLM_CALLS`)

```python
# Error classification example
def _classify_error(stderr: str) -> str:
    if "SyntaxError" in stderr:    return "syntax"
    if "AttributeError" in stderr: return "attribute"
    if "TypeError" in stderr:      return "type"
    return "runtime"
```

### Duration sync (`generator/scene_generator.py`)

Narration audio and animation video have to match duration or the muxer has to distort one of them. Rather than fixing this post-render, the generator estimates narration duration before generating code and passes it as a hard constraint:

```python
_WORDS_PER_MINUTE = 130

def _estimate_narration_duration(narration: str) -> int:
    words = len(narration.split())
    return max(10, math.ceil(words / _WORDS_PER_MINUTE * 60))
```

The scene generator prompt then instructs the LLM to distribute `self.wait()` calls to hit exactly that duration.

---

## PDF ingestion (`input/pdf_parser.py`)

Converts academic PDFs (lecture notes, papers) into structured chunks for lesson planning:

1. **Extract** — `pypdf` reads page text, skips image-only pages with a warning
2. **Clean** — fix hyphenation breaks, remove page numbers, collapse whitespace
3. **Chunk** — heading-based segmentation (numbered sections, Chapter/Section prefixes, all-caps lines), falls back to paragraph-density chunking
4. **Cap** — content truncated at 24k chars before LLM call to control token cost

The PDF planner prompt enforces a 9-stage explanation arc: hook → intuition → formalism → worked example → mistakes → deeper insight → complex example → edge cases → summary.

---

## Audio-video sync (`renderer/muxer.py`)

The muxer never speed-warps either stream — pitch-shifted narration and
time-distorted animation both look wrong. It pads instead:

| Condition | Strategy |
|---|---|
| Audio ≤ video duration | Pad audio with silence (`apad=whole_dur=...`) |
| Audio > video duration | Freeze the last video frame (`tpad=stop_mode=clone`) |
| Video missing or unreadable | Synthesize a solid-colour background for the audio |

Logs a WARNING at a duration diff > 1.0s and a LARGE MISMATCH at > 1.5s — a
large mismatch indicates the narration duration estimator or the generator
prompt needs tuning.

---

## Project structure

```
manimgen/
├── manimgen/
│   ├── cli.py                    # entry point: manimgen <topic> | --pdf file.pdf
│   ├── llm.py                    # provider toggle: gemini | anthropic | ollama
│   ├── input/
│   │   ├── parser.py             # topic string normalization
│   │   └── pdf_parser.py         # PDF → cleaned text chunks
│   ├── planner/
│   │   ├── lesson_planner.py     # topic/PDF → structured lesson plan JSON
│   │   └── prompts/
│   │       ├── planner_system.md         # topic planner prompt
│   │       ├── planner_pdf_system.md     # PDF planner prompt (9-stage arc)
│   │       ├── researcher_system.md      # Panel of Experts knowledge brief
│   │       └── storyboard_critic_system.md
│   ├── generator/
│   │   ├── scene_generator.py    # section JSON → ManimGL .py file
│   │   └── prompts/
│   │       └── director_system.md   # full ManimGL API reference prompt
│   ├── validator/
│   │   ├── codeguard.py          # static analysis + 20+ auto-fixes
│   │   ├── scene_ast_gate.py     # AST-level structural gate
│   │   ├── runner.py             # manimgl subprocess + logging
│   │   ├── retry.py              # error classification + LLM repair loop
│   │   ├── frame_checker.py      # zero-cost PIL black/frozen/clipping checks
│   │   ├── layout_checker.py     # LLM vision check on rendered frames
│   │   ├── timing_verifier.py    # static timing analysis + auto-fix
│   │   ├── fallback.py           # title card fallback scene
│   │   └── env.py                # render environment setup
│   ├── renderer/
│   │   ├── tts.py                # edge-tts narration + word timestamps
│   │   ├── audio_slicer.py       # full audio → cue-aligned slices
│   │   ├── cutter.py             # rendered .mp4 → per-cue clips
│   │   ├── muxer.py              # audio-video sync (pad-only, no speed warp)
│   │   └── assembler.py          # ffmpeg concat of section videos
│   └── editor/
│       ├── server.py             # Flask clip editor server
│       └── templates/editor.html # browser-based trim/reorder UI
├── examples/                     # 32 hand-written ManimGL scenes (few-shot seeds)
├── tests/                        # unit tests, zero LLM/subprocess calls
└── config.yaml                   # LLM provider, TTS voice, render quality
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Animation engine | [ManimGL](https://github.com/3b1b/manim) (3b1b version, not ManimCommunity) |
| LLM — default | Google Gemini 2.5 Flash (`llm_provider: "gemini"`) |
| LLM — alternatives | Anthropic Claude Sonnet, or a local Ollama model |
| TTS | Microsoft edge-tts (Neural voices) |
| Video processing | FFmpeg |
| PDF parsing | pypdf |
| Clip editor | Flask + vanilla JS |
| Tests | pytest (`python3 -m pytest -q`) |
| Output format | H.264, 1920×1080, 60fps |

---

## Setup

```bash
git clone https://github.com/Varkot-dev/videomaking.git
cd videomaking

pip install -e .
pip install pypdf edge-tts google-genai anthropic pyyaml flask

# Set your LLM provider.
# The default provider is Gemini (config.yaml: llm_provider: "gemini"),
# so GEMINI_API_KEY is the only key a stock checkout needs. Put it in
# manimgen/.env or export it:
export GEMINI_API_KEY=your_key

# Anthropic and Ollama are also fully implemented. To use one instead:
# export LLM_PROVIDER=anthropic && export ANTHROPIC_API_KEY=your_key
# export LLM_PROVIDER=ollama     # local Ollama server, no API key needed
```

Resolution order for the provider is `LLM_PROVIDER` env var → `llm_provider` in
`config.yaml` → default `"gemini"`. Model names live under `llm:` in
`config.yaml` and are never hardcoded.

**Dependencies:** FFmpeg and BasicTeX (for LaTeX rendering in ManimGL)
```bash
brew install ffmpeg
brew install --cask basictex
```

---

## Usage

```bash
# Topic mode — cheapest, best for testing
manimgen "binary search"
manimgen "depth-first search"
manimgen "dynamic programming"

# PDF mode — from lecture notes
manimgen --pdf lecture.pdf

# Disable TTS for faster iteration (no audio)
# set tts.enabled: false in config.yaml

# Cap LLM retry calls (reduces cost during testing)
export MANIMGEN_MAX_RETRY_LLM_CALLS=0   # deterministic fixes only, no LLM retries

# Edit rendered clips before final export
manimgen-edit                           # auto-loads muxed/ or videos/
manimgen-edit --videos path/to/clips/
```

Output: `manimgen/output/videos/<title>.mp4`

---

## Testing

```bash
python3 -m pytest -q
```

`pyproject.toml` is the single source of truth for how the suite runs — a bare
`pytest` runs the exact set CI runs. Do not add `--ignore` flags; they hide
local↔CI divergence. The suite makes zero LLM or subprocess calls (fully
mocked), so it is free to run. Tests cover:
- Every codeguard auto-fix and banned pattern
- Error-aware repair from real stderr tracebacks  
- Section cap enforcement in the planner
- Narration duration estimation
- Muxer strategy selection per duration ratio
- PDF parser output structure and chunking logic
- Documentation accuracy (`tests/test_docs_accuracy.py` — every relative path
  the docs cite must exist, no doc may hardcode a test count, and no tracked
  doc may contain a personal home-directory path)

### Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request:

| Job | What it does |
|---|---|
| `test` | `python -m pytest -q` on Python 3.11, 3.12, and 3.13 |
| `lint` | `ruff check` (advisory), plus a blocking pass for `E9,F63,F7,F82` — real syntax/undefined-name errors |
| `secrets` | Scans the tree for credential patterns and asserts `.env` is untracked |

CI needs no API keys: the suite is fully mocked, makes no network calls, and
renders nothing.

---

## Cost model

Each `manimgen` run makes approximately `2 + (N × 1.5)` LLM calls where N = number of sections:
- 1 call for lesson planning
- 1 call per section for code generation
- ~0.5 calls/section average for retries (with `MAX_LLM_FIX_CALLS=1`)

At Gemini Flash pricing, a 5-section topic run costs ~$0.02. A 10-section PDF run costs ~$0.05. Running against a large astrophysics paper that generated 19 sections cost ~$0.15.

Set `tts.enabled: false` in `config.yaml` to skip narration during development — TTS adds no LLM cost but takes 30–60s per section.
