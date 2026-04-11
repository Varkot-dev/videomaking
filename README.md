# ManimGen

> **Any topic or PDF → a narrated, 3Blue1Brown-style animated video. Fully automated.**

ManimGen is a production-grade pipeline that converts a topic string or PDF of lecture notes into a rendered `.mp4` with voiceover and synchronized animations. It uses an LLM to write [ManimGL](https://github.com/3b1b/manim) animation code, then repairs, validates, and renders it without human intervention.

---

## Demo

> _Demo video coming soon_

**Example outputs:**
- `manimgen "gradient descent"` → ~6 min animated explainer
- `manimgen "the Fourier transform"` → ~8 min with 3D surfaces and LaTeX equations
- `manimgen --pdf lecture.pdf` → 9-stage structured lesson from academic notes

---

## What makes this hard

ManimGL — the animation library Grant Sanderson uses for 3Blue1Brown — has a narrow, finicky API that is significantly underrepresented in LLM training data. Its community fork (ManimCommunity) is far more common in tutorials, so LLMs routinely generate community-fork code that doesn't compile against ManimGL.

Raw LLM output fails on first render ~60% of the time with errors like:

| LLM-generated (wrong) | ManimGL-correct |
|---|---|
| `from manim import *` | `from manimlib import *` |
| `Create(circle)` | `ShowCreation(circle)` |
| `MathTex(r"x^2")` | `Tex(r"x^2")` |
| `self.set_camera_orientation(phi=60*DEGREES)` | `self.frame.reorient(theta, phi)` |
| `Axes(x_length=7, y_length=4)` | `Axes(width=7, height=4)` |
| `FadeOut(a, b)` | `FadeOut(a), FadeOut(b)` |

ManimGen's core engineering contribution is a multi-stage validation and repair system that catches and fixes these failures deterministically — without spending extra LLM tokens — and falls back to LLM-guided repair only for errors that can't be pattern-matched.

---

## Architecture

### Audio-first CUE pipeline

The pipeline is **audio-first**: the narration is synthesized first, word-level timestamps are extracted, and animation durations are derived from those timestamps. Animations never get speed-warped to match audio — audio defines the ground truth and video is built to fit.

```
topic / PDF
  │
  ├─► Researcher          LLM → structured knowledge brief
  │   (Panel of Experts prompt: professor + pedagogy expert + explainer creator)
  │
  ├─► Lesson Planner      LLM → storyboard JSON with:
  │                         - narration with [CUE] markers
  │                         - pixel-level visual descriptions per cue
  │
  ├─► CUE Parser          strips [CUE] → clean narration + cue word indices
  │
  └─► For each section:
        │
        ├─► TTS (edge-tts)        full narration .mp3 + per-word timestamps (sub-ms)
        ├─► Segmenter              word onsets → exact duration per cue
        ├─► Audio Slicer           full audio → N cue-aligned .m4a clips
        │
        ├─► Director (LLM)        one call → one complete ManimGL Scene class
        │   - receives storyboard + all cue durations
        │   - writes self.wait() pauses at each cue boundary
        │   - few-shot seeded from 30+ hand-written example scenes
        │
        ├─► Codeguard              token-free static analysis + 20+ auto-fixes
        ├─► Runner                 subprocess: manimgl → section.mp4
        ├─► Retry loop             codeguard → error-aware fix → LLM fix → fallback
        │
        ├─► Frame Checker          PIL-based: black frames, frozen frames, edge clipping
        ├─► Layout Checker         LLM vision: multi-frame sampling → ISSUE|CAUSE|FIX feedback
        │
        ├─► Cutter                 FFmpeg → N per-cue video clips
        └─► Muxer                  audio + video → section_cue00.mp4, _cue01.mp4, ...
              (pad-only, no speed warp)
                │
                ▼
          Assembler        normalize 1920×1080@60fps + xfade → final.mp4
                │
                ▼
          manimgen-edit    Flask browser UI for clip review, reorder, trim, export
```

**Key architectural decisions:**

- **One Scene per section, not per cue.** The Director generates one continuous animation. Axes build once. Cues animate on top of what's already present — no scene restarts.
- **Template engine removed.** The Director writes raw ManimGL Python. Visual variety comes from the storyboard, not templates.
- **Codeguard before everything.** Every generated file goes through static analysis before any render attempt. Zero token cost.
- **Retry always reloads from disk.** After each fix (codeguard or LLM), the file is re-read — prevents silent discard of in-place codeguard patches.
- **Render cache with hash sidecar.** Stale renders (different topic) are detected and re-rendered automatically.

---

## Codeguard — token-free repair

`validator/codeguard.py` runs regex + AST analysis on generated code and fixes known-bad patterns before any render attempt. This eliminates ~70% of render failures without spending a single token.

| Pattern | Auto-fix |
|---|---|
| `from manim import *` | → `from manimlib import *` |
| `MathTex(...)` | → `Tex(...)` |
| `Create(...)` | → `ShowCreation(...)` |
| `self.set_camera_orientation(phi=X, theta=Y)` | → `self.frame.reorient(Y, X)` |
| `reorient(theta_deg=..., phi_deg=...)` | → `reorient(theta_degrees=..., phi_degrees=...)` |
| `Axes(x_length=7, y_length=4)` | → `Axes(width=7, height=4)` |
| `FadeOut(a, b)` | → `FadeOut(a), FadeOut(b)` |
| `self.play(obj.become(...))` | → `obj.become(...); self.play(ShowCreation(obj))` |
| `NumberLine(..., label="x")` | → strip `label=` kwarg |
| `set_fill_color(...)` | → `set_fill(...)` |
| `self.wait(-0.2)` | → `self.wait(0.01)` |
| `Tex(r"\text{label}")` outer wrapper | → `Tex(r"label")` |

Patterns that can't be auto-fixed are classified and escalated to the LLM repair loop with targeted guidance.

---

## Retry loop

When codeguard alone can't fix a scene, the retry loop runs:

```
1. codeguard auto-fix          (token-free, always first)
2. error-aware fix             (token-free, pattern-matched stderr)
3. LLM fix                     (budget-capped, error + guidance → targeted prompt)
4. visual fix                  (if frame/layout checker finds defects)
5. fallback scene              (styled title card with TTS if all retries fail)
```

Error types are classified via `SceneErrorType` enum: `PRECHECK_VGROUP`, `SYNTAX`, `IMPORT`, `ATTRIBUTE`, `TYPE`, `RUNTIME` — each gets a different fix strategy and prompt fragment.

---

## Visual validation — two tiers

**Tier 1 — Frame Checker (`validator/frame_checker.py`)**
Zero-cost PIL-based checks that run after every render:
- Black frame detection (render crashed mid-scene)
- Frozen frame detection (animation stalled)
- Edge clipping detection (content outside safe zone)

**Tier 2 — Layout Checker (`validator/layout_checker.py`)**
LLM vision pass on multi-frame samples (25%/50%/75% of scene duration):
- Returns structured `ISSUE | CAUSE | FIX` feedback
- Feedback is injected into the next retry prompt

---

## A/V sync contracts

Five contracts are tested and enforced:

| Contract | Why |
|---|---|
| Cue 0 measured from `0.0` | pre-speech silence is preserved |
| Segment boundary uses `word.end` | last syllable is never clipped |
| Slicer re-encodes to AAC | eliminates ~26ms MP3 frame-boundary drift |
| Muxer `tpad` uses gap duration | not total audio duration |
| Assembler enforces `-ar 48000` | prevents sample-rate drift across sections |

Muxer strategy by duration mismatch:
- Video longer than audio → trim video to audio
- Audio longer than video → freeze last frame (`tpad`)
- Logs WARNING at diff > 1.0s, LARGE MISMATCH at diff > 1.5s

---

## Setup

### Prerequisites

```bash
brew install ffmpeg
brew install --cask basictex   # LaTeX for ManimGL equation rendering
```

ManimGL also requires OpenGL. Works out of the box on macOS. On Linux, install `libgl1-mesa-glx`.

### Install

```bash
git clone https://github.com/Varkot-dev/videomaking.git
cd videomaking/manimgen

pip install -e .
```

### API keys

Create `manimgen/.env`:

```
GEMINI_API_KEY=your_key_here
```

Or for Anthropic Claude (switch `llm_provider` in `config.yaml`):

```
ANTHROPIC_API_KEY=your_key_here
```

---

## Usage

```bash
# Topic mode
manimgen "gradient descent"
manimgen "the Fourier transform"
manimgen "binary search trees"

# PDF mode — from lecture notes or papers
manimgen --pdf notes.pdf

# Resume from cached plan (skip replanning)
manimgen --resume

# Cap LLM retry calls (faster/cheaper iteration)
MANIMGEN_MAX_RETRY_LLM_CALLS=0 manimgen "topic"   # codeguard-only, no LLM retries

# Launch clip editor
manimgen-edit
```

**Output:** `manimgen/output/videos/<title>.mp4`

Intermediate files:
- Scene code + logs: `manimgen/output/scenes/`, `manimgen/output/logs/`
- Per-cue muxed clips: `manimgen/output/muxed/`
- Lesson plan: `manimgen/output/plan.json`

---

## Clip editor

`manimgen-edit` launches a local Flask server with a browser UI for reviewing rendered clips before final export. Supports:
- Per-cue clip playback
- Drag-to-reorder clips across sections
- Trim start/end
- Export final assembly

---

## Configuration

`config.yaml`:

```yaml
llm_provider: "gemini"       # "gemini" | "anthropic"

llm:
  gemini_model: "gemini-2.5-flash"
  anthropic_model: "claude-sonnet-4-6"

rendering:
  quality: hd                # hd = 1080p | l = 720p | m = 480p
  fps: 60
  max_retries: 3

tts:
  engine: edge-tts
  voice: en-US-AndrewMultilingualNeural
  speed: "+5%"
  enabled: true              # set false to skip TTS during development
```

---

## Testing

399 tests, all passing, zero LLM or subprocess calls (fully mocked):

```bash
# Full suite (skip real-LLM tests)
python3 -m pytest tests/ \
  --ignore=tests/test_scene_generator.py \
  --ignore=tests/test_planner.py \
  --ignore=tests/test_pipeline_e2e.py -q

# By area
python3 -m pytest tests/test_codeguard.py -v          # auto-fixes + banned patterns
python3 -m pytest tests/test_pipeline_contracts.py -v  # A/V sync contracts
python3 -m pytest tests/test_frame_checker.py -v       # zero-cost visual checks
python3 -m pytest tests/test_timing_verifier.py -v     # static timing analysis
```

---

## Cost

Each run makes approximately `2 + (N × 1.5)` LLM calls, where N = number of sections:
- 1 call for research
- 1 call for lesson planning
- 1 call per section for scene generation
- ~0.5 calls/section average for retries

At Gemini 2.5 Flash pricing:
- 5-section topic run: ~$0.02
- 10-section PDF run: ~$0.05

Set `tts.enabled: false` in `config.yaml` to skip narration during development. TTS adds no LLM cost but takes 30–60s per section.

---

## Tech stack

| Layer | Technology |
|---|---|
| Animation engine | ManimGL (3b1b fork) |
| LLM (default) | Google Gemini 2.5 Flash |
| LLM (alternate) | Anthropic Claude Sonnet 4.6 |
| TTS | Microsoft edge-tts — `en-US-AndrewMultilingualNeural` |
| Video processing | FFmpeg 8.1 |
| PDF parsing | pypdf + pymupdf |
| Visual validation | PIL (frame checks) + LLM vision (layout checks) |
| Clip editor | Flask + vanilla JS |
| Tests | pytest — 399 tests |
| Output | H.264, 1920×1080, 60fps |
| Language | Python 3.13 |

---

## Project structure

```
manimgen/
├── manimgen/                    # importable package
│   ├── cli.py                   # entry: manimgen <topic> | --pdf | --resume
│   ├── llm.py                   # Gemini/Anthropic toggle, config-driven
│   ├── utils.py                 # strip_fencing(), section_class_name()
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
│   │   └── prompts/             # director_system.md (full ManimGL API reference)
│   ├── validator/
│   │   ├── codeguard.py         # static checks + 20+ auto-fixes
│   │   ├── runner.py            # subprocess manimgl with -c "#1C1C1C"
│   │   ├── retry.py             # retry loop: codeguard → error-aware fix → LLM fix → fallback
│   │   ├── fallback.py          # styled bullet-point fallback scene
│   │   ├── layout_checker.py    # LLM vision check on rendered frames (multi-frame)
│   │   ├── frame_checker.py     # zero-cost PIL-based black/frozen/clipping detection
│   │   ├── timing_verifier.py   # static AST timing analysis
│   │   └── prompts/             # retry_system.md, layout_checker_system.md
│   ├── renderer/
│   │   ├── tts.py               # edge-tts + WordBoundary per-word timestamps
│   │   ├── audio_slicer.py      # full audio → N cue-aligned .m4a clips (AAC)
│   │   ├── cutter.py            # section .mp4 → per-cue clips (parallel FFmpeg)
│   │   ├── muxer.py             # audio+video mux (pad-only, no speed warp)
│   │   └── assembler.py         # normalize 1920×1080@60fps, xfade sections
│   └── editor/
│       ├── server.py            # Flask UI
│       └── templates/editor.html
├── examples/                    # 30+ hand-written verified ManimGL scenes
│                                # Each has `techniques: <name>` in class docstring
│                                # — scene_generator indexes them at runtime for few-shot
├── tests/                       # 399 pytest tests
├── config.yaml
├── requirements.txt
└── setup.py
```

---

## License

MIT
