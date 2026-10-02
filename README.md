# ManimGen

An automated pipeline that converts a topic string or PDF of lecture notes into a narrated, animated CS explainer video in the style of 3Blue1Brown.

**Input:** `"binary search"` or `lecture.pdf`  
**Output:** A rendered `.mp4` with voiceover, 5–10 minutes of animated content  
**LLM:** Claude through your Claude subscription by default (Claude Code in headless mode, no API key), with the Anthropic API, Gemini and Ollama as alternatives

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
videomaking/                      # git root (this README)
└── manimgen/                     # Python project root: run pip, pytest and manimgen from here
    ├── manimgen/                 # source package
    │   ├── cli.py                # entry: manimgen <topic> | --pdf <file> | --resume
    │   ├── llm.py                # shared LLM client (claude_cli / anthropic / gemini / ollama)
    │   ├── input/
    │   │   ├── parser.py         # normalize topic string
    │   │   └── pdf_parser.py     # PDF → cleaned text chunks (heading-based segmentation)
    │   ├── planner/
    │   │   ├── lesson_planner.py # research_topic() + plan_lesson() → storyboard JSON
    │   │   ├── cue_parser.py     # parse [CUE] markers → cue_word_indices
    │   │   ├── segmenter.py      # word timestamps + cue indices → CueSegment durations
    │   │   └── prompts/          # planner_system.md, planner_pdf_system.md, researcher_system.md
    │   ├── generator/
    │   │   ├── scene_generator.py# Director: LLM → one ManimGL Scene per section
    │   │   └── prompts/          # director_system.md
    │   ├── validator/
    │   │   ├── codeguard.py      # static analysis + 50+ auto-fixes
    │   │   ├── manimlib_signatures.py # type-aware kwarg introspection (Phase 2 shadow)
    │   │   ├── manimlib_symbols.py    # call-target name validation
    │   │   ├── scene_ast_gate.py # security: AST allowlist for top-level statements
    │   │   ├── timing_verifier.py# loop-aware cue timing analysis + auto-fix
    │   │   ├── render_validator.py # unified post-render quality gate (frame + layout)
    │   │   ├── frame_checker.py  # zero-cost PIL: black/frozen/clipping detection
    │   │   ├── layout_checker.py # LLM vision: overlap/overflow/layout defect detection
    │   │   ├── runner.py         # manimgl subprocess with -c #1C1C1C flag
    │   │   ├── retry.py          # retry loop: codeguard → timing → error fix → LLM fix
    │   │   ├── fallback.py       # styled bullet-point fallback scene (with TTS)
    │   │   └── env.py            # render environment (cross-platform PATH, LaTeX lookup)
    │   ├── renderer/
    │   │   ├── tts.py            # edge-tts with WordBoundary → per-word timestamps
    │   │   ├── audio_slicer.py   # full audio → N cue-aligned .m4a slices (AAC)
    │   │   ├── cutter.py         # cut rendered section .mp4 into per-cue clips
    │   │   ├── muxer.py          # audio+video mux (pad-only, no speed warp)
    │   │   └── assembler.py      # normalize 1920x1080@60fps, xfade transitions
    │   └── editor/
    │       ├── server.py         # Flask clip editor server
    │       └── templates/editor.html # browser-based trim/reorder/export UI
    ├── manimgen/examples/        # hand-written verified ManimGL scenes (Director few-shot)
    │                             # Each has `techniques: <name>` in class docstring
    ├── tests/                    # unit + integration tests, zero LLM or subprocess calls
    ├── docs/
    │   └── KNOWN_ISSUES.md       # active failure log + env-doctor guards
    ├── scripts/
    │   └── env_doctor.py         # setup health checks (imports, ffmpeg, claude CLI, etc.)
    ├── config.yaml               # LLM provider, model names, TTS config, render quality
    ├── requirements.txt          # runtime dependencies (setup.py reads this)
    ├── requirements-dev.txt      # requirements.txt + pytest, pytest-mock, hypothesis, ruff
    └── setup.py                  # console_scripts: manimgen, manimgen-edit
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Animation engine | [ManimGL](https://github.com/3b1b/manim) 1.7.2 (3b1b version, not ManimCommunity) |
| LLM, default | Claude via [Claude Code](https://claude.com/claude-code) in headless mode (`claude -p`), billed to your Claude plan's usage limits; no API key |
| LLM, also supported | Anthropic API (`claude-sonnet-5-5`), Google Gemini 2.5 Flash, and Ollama for fully local runs |
| TTS | Microsoft edge-tts (Neural voices, WordBoundary timestamps) |
| Video processing | FFmpeg (ffmpeg + ffprobe) |
| LaTeX (optional) | BasicTeX / TeX Live on macOS and Linux, MiKTeX on Windows; only needed for `Tex()` formulas |
| PDF parsing | pypdf, PyMuPDF |
| Clip editor | Flask + vanilla JS |
| Tests | pytest, fully mocked, zero API cost (`python3 -m pytest -q`) |
| Lint | ruff 0.15.13 (pinned in `requirements-dev.txt`, same version CI uses) |
| Output format | H.264, 1920×1080, 60fps |

---

## LLM providers

The provider is picked in this order (first match wins):

1. the `LLM_PROVIDER` environment variable
2. `llm_provider` in `config.yaml`
3. the built-in default, `claude_cli`

Model names live under `llm:` in `config.yaml`, never in code.

| Provider | What it runs | Needs | Billing |
|---|---|---|---|
| `claude_cli` (default) | `claude -p` (Claude Code, headless) | Claude Code installed and logged in | Your Claude plan's usage limits (Pro / Max), not per token |
| `anthropic` | Anthropic API, `claude-sonnet-5-5`, `max_tokens` 16000 | `ANTHROPIC_API_KEY` | Per token |
| `gemini` | Google Gemini, `gemini-2.5-flash` | `GEMINI_API_KEY` | Per token |
| `ollama` | Local Ollama server (`llama3.1` by default) | A running Ollama install | Free, lower quality |

### How the `claude_cli` provider works

Every LLM call in the pipeline (research, planning, scene generation, retries, and the vision layout check) becomes one `claude -p` subprocess:

- The system prompt is written to a temporary file and passed with `--system-prompt-file`. The user turn, plus any frames as base64 PNG images, goes in on stdin as a `stream-json` message. No prompt text is put on the command line, which matters on Windows where a command line is capped at about 32K characters.
- Claude Code runs as a plain completion: `--tools=` disables every tool, `--strict-mcp-config` loads no MCP servers, and `--no-session-persistence` keeps the calls out of your session history.
- The working directory is an empty temporary folder and `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` is set, so no repo or user CLAUDE.md, settings or hooks leak into the prompt.
- `ANTHROPIC_API_KEY` is deliberately removed from the subprocess environment. If it were passed through, Claude Code would quietly switch to per-token API billing instead of your subscription (for example when the key is in `.env` for the `anthropic` provider).
- Each call has a 600 second timeout and is tried up to 3 times.

**Setup:** install Claude Code (see the setup sections below), then run `claude` once in a terminal and log in with your Claude account. After that `manimgen` needs nothing else.

**Opus or Sonnet:** set `llm.claude_cli_model` in `config.yaml`. The default is `sonnet`. Use `opus` for the strongest scene code at the cost of using your plan's limits faster. A full model ID also works.

```yaml
llm_provider: "claude_cli"
llm:
  claude_cli_model: "opus"     # or "sonnet" (default)
  claude_cli_path: "claude"    # executable name, or a full path if claude is not on PATH
```

**Switching providers** for one run, without editing `config.yaml`:

```bash
# macOS / Linux
LLM_PROVIDER=ollama manimgen "binary search"          # local and free

# Paid per-token providers are blocked unless you opt in with MANIMGEN_ALLOW_PAID_API=1
MANIMGEN_ALLOW_PAID_API=1 LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=your_key manimgen "binary search"
MANIMGEN_ALLOW_PAID_API=1 LLM_PROVIDER=gemini GEMINI_API_KEY=your_key manimgen "binary search"
```

```powershell
# Windows PowerShell (lasts for this terminal window only)
$env:MANIMGEN_ALLOW_PAID_API = "1"   # required: gemini and anthropic bill per token
$env:LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "your_key"
manimgen "binary search"
```

API keys can also go in a `.env` file in the `manimgen/` project folder (`GEMINI_API_KEY=...`, `ANTHROPIC_API_KEY=...`); it is git-ignored and loaded automatically.

---

## Setup: macOS / Linux

```bash
git clone https://github.com/Varkot-dev/videomaking.git
cd videomaking/manimgen

python3 -m venv .venv
source .venv/bin/activate

pip install -r requirements-dev.txt   # runtime deps + pytest, pytest-mock, hypothesis, ruff
pip install -e .                      # installs the manimgen and manimgen-edit commands
```

`requirements.txt` already pins `manimgl==1.7.2` and `setuptools<81` (manimgl 1.7.2 imports `pkg_resources`, which setuptools 81 removed). Use `pip install -r requirements.txt` instead of the dev file if you only want to run the pipeline.

**System dependencies:** FFmpeg, and optionally a LaTeX distribution for `Tex()` formulas.

```bash
# macOS
brew install ffmpeg
brew install --cask basictex

# Debian / Ubuntu (manimgl also needs the pango/cairo headers to build)
sudo apt-get install ffmpeg pkg-config libpango1.0-dev libcairo2-dev texlive-latex-extra
```

**Claude Code** (for the default provider):

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude      # run once, log in with your Claude account, then exit
```

Verify the install:

```bash
manimgen --help              # must print usage, not ModuleNotFoundError
python3 -m pytest -q         # full suite, mocked, no API key or Claude login required
python3 scripts/env_doctor.py   # checks manimlib, ffmpeg/ffprobe, the claude CLI, etc.
```

---

## Setup: Windows 10 / 11 (no admin rights needed)

Everything below installs into your own user profile, so it works on a locked-down machine such as a school or library PC. Use **PowerShell** (Start menu, type "PowerShell"). After each install that changes PATH, close and reopen PowerShell so it sees the change.

### 1. Python 3.11

1. Download the **Windows installer (64-bit)** for the latest Python 3.11 release from [python.org/downloads/windows](https://www.python.org/downloads/windows/). 3.11 is the safest choice: every native dependency (manimgl's pycairo, ManimPango, PyMuPDF) ships prebuilt wheels for it.
2. On the first installer screen, **tick "Add python.exe to PATH"** and **untick "Use admin privileges when installing py.exe"**.
3. Choose **Customize installation**, keep the defaults, and on the "Advanced Options" page make sure **"Install Python for all users" is unticked**. That is the "install for current user" mode and needs no admin. Then Install.
4. Check in a new PowerShell window: `python --version`.

### 2. Git for Windows

Download from [git-scm.com/download/win](https://git-scm.com/download/win). Either:

- run the normal installer: without admin it installs per user into your AppData folder, or
- take the **Portable** ("thumbdrive edition") build, unpack it anywhere in your user folder, and add its `cmd` folder to your user PATH (see step 3 for how).

Claude Code on Windows also uses Git Bash, so install Git before Claude Code. Check: `git --version`.

### 3. FFmpeg

1. From [gyan.dev/ffmpeg/builds](https://www.gyan.dev/ffmpeg/builds/), download **ffmpeg-release-essentials.zip**.
2. Unzip it under your user profile, for example to `%USERPROFILE%\tools\ffmpeg` so that `ffmpeg.exe` sits in `%USERPROFILE%\tools\ffmpeg\bin`.
3. Add that `bin` folder to your **user** PATH: Start menu, type "environment", open **"Edit environment variables for your account"** (not the "system" one, which needs admin). Select `Path` under "User variables", click Edit, New, paste the full `...\tools\ffmpeg\bin` path, OK.
4. Check in a new PowerShell window: `ffmpeg -version` and `ffprobe -version`.

### 4. Claude Code (for the default provider)

```powershell
irm https://claude.ai/install.ps1 | iex
```

The native installer is per user (it puts `claude.exe` under `%USERPROFILE%\.local\bin`) and needs no admin. If a new window says `claude` is not recognized, add `%USERPROFILE%\.local\bin` to your user PATH the same way as FFmpeg. Then run `claude` once, log in with your Claude account, and exit.

### 5. LaTeX (optional)

Only scenes that use `Tex()` formulas need LaTeX; `Text()` does not. Install [MiKTeX](https://miktex.org/download) and pick **"Install only for me"** (per-user, no admin). During setup set "Install missing packages on the fly" to **Yes**, so the packages ManimGL needs are fetched on first use. Check: `latex --version`. The render environment finds `latex` through PATH on every OS.

### 6. Clone and install manimgen

```powershell
git clone https://github.com/Varkot-dev/videomaking.git
cd videomaking\manimgen

python -m venv .venv
.venv\Scripts\activate
```

If activation fails with "running scripts is disabled on this system", allow your own scripts for your account only (no admin needed) and try again:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
.venv\Scripts\activate
```

If the machine's policy still blocks it, skip activation and call the venv's Python directly for every command, for example `.venv\Scripts\python.exe -m pip ...` and `.venv\Scripts\manimgen.exe "binary search"`.

Install the dependencies and the package:

```powershell
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
pip install -e .
```

### 7. Verify

```powershell
manimgen --help
python -m pytest -q
python scripts\env_doctor.py
```

`manimgen --help` must print usage, the test suite needs no API key or Claude login, and env-doctor warns about anything still missing from PATH (manimgl, ffmpeg, ffprobe, claude).

**Windows notes:**

- ManimGL renders with OpenGL. Integrated Intel graphics (UHD 7xx and newer) are fine; rendering a 1080p60 section takes longer than on a discrete GPU, so set `rendering.quality: m` in `config.yaml` for faster drafts.
- Shared PCs that reset the user profile on logout will lose these installs; keep the clone and tools on a USB drive or expect to repeat the setup.

---

## Usage

Run from the `manimgen/` project folder with the venv active.

```bash
# Topic mode, best for testing
manimgen "binary search"
manimgen "gradient descent"
manimgen "dynamic programming"

# PDF mode, from lecture notes or papers
manimgen --pdf lecture.pdf

# Resume a previous run from cached plan
manimgen --resume

# Cap LLM retry calls (saves usage during testing)
export MANIMGEN_MAX_RETRY_LLM_CALLS=0   # deterministic fixes only, no LLM retries

# Enable Phase 3 kwarg enforcement (strips provably-invalid kwargs before render)
export MANIMGEN_KWARG_ENFORCE=1

# Adjust freeze-frame detection threshold (default: 2.5s)
export MANIMGEN_FREEZE_BLOCK_THRESHOLD=2.0

# Edit rendered clips before final export
manimgen-edit                           # auto-loads muxed/ or videos/
manimgen-edit --videos path/to/clips/
```

On Windows PowerShell, set the variables with `$env:MANIMGEN_MAX_RETRY_LLM_CALLS = "0"` instead of `export`.

Output: `manimgen/output/videos/<title>.mp4`

### Run summary and exit codes

At the end of every run `manimgen` prints a summary with one line per section
and writes a run manifest next to the video,
`manimgen/output/videos/run_manifest.json` (also when no video was produced).
Each section ends in one of these states:

| Status | Meaning |
|---|---|
| `ok` | Rendered and narrated (first pass, repaired by a retry, or reused from the cache) |
| `accepted_with_defects` | In the video, but the retry loop accepted it with known defects (for example a freeze-frame tail) |
| `fallback` | A title card stands in for the animation because the render and its retries failed |
| `dropped` | Nothing from this section is in the video |
| `silent` | In the video, but without narration |
| `errored` | An unexpected error stopped this section |

The exit code tells a script what happened without reading the log:

| Code | Meaning |
|---|---|
| `0` | Every section is `ok` or `accepted_with_defects` (the latter are listed in the summary) |
| `1` | Refused to run (for example a `--resume` mismatch) or no video was produced |
| `2` | Bad command line arguments |
| `3` | A video was produced, but a section is `fallback`, `dropped`, `silent` or `errored` |
| `4` | Stopped cleanly on a usage limit (Claude plan allowance, overage guard or paid-API guard); nothing was assembled |

An error in one section (a failed LLM call, a failed ffmpeg cut) no longer ends
the run: that section is reported as `errored` or `dropped`, the other sections
are assembled into a partial video, and the exit code is 3. A usage limit is
different, because every later call would fail too: the run stops before the
next section, prints the reset time when it is known, and exits 4. Finished
sections stay cached, so once the limit resets `manimgen --resume` builds only
what is missing (when planning itself hit the limit there is no plan yet, so
rerun the same command instead).

A cached section keeps the status it was built with, so a fallback card reused
by `--resume` is still reported as `fallback`.

---

## Testing

From the `manimgen/` project folder:

```bash
python3 -m pytest -q             # the full suite, exactly what CI runs
ruff check manimgen/             # lint (same pinned ruff version as CI)
ruff format --check manimgen/    # formatting
```

Run pytest bare. `pyproject.toml` scopes it to `tests/` with `--strict-markers` and no `--ignore` flags, and CI runs the entire suite the same way. Every LLM call, subprocess and network seam is mocked, so the suite costs nothing, needs no API key or Claude login, and never renders video.

The suite covers:
- Every codeguard auto-fix and banned pattern
- Type-aware kwarg introspection (manimlib_signatures)
- Loop-aware timing analysis and auto-fix (timing_verifier)
- AST security gate (scene_ast_gate)
- Error-aware repair from real stderr tracebacks
- Section cap enforcement in the planner
- A/V sync contracts (muxer, slicer, segmenter)
- Frame defect detection (frame_checker)
- PDF parser output structure and chunking logic
- The LLM provider switch, including how `claude -p` is invoked
- Documentation accuracy (`tests/test_docs_accuracy.py`: cited paths exist, no stale counts)

---

## Cost model

Each `manimgen` run makes approximately `2 + (N × 1.5)` LLM calls where N = number of sections:
- 1 call for research
- 1 call for lesson planning
- 1 call per section for scene generation
- ~0.5 calls/section average for retries (with `MAX_LLM_FIX_CALLS=1`)
- plus a vision layout check when a rendered scene fails the free frame checks

**With `claude_cli` (default)** there is no per-token bill. Each call counts against your Claude plan's usage limits, the same limits your normal Claude and Claude Code use draws from. A long PDF run, or many runs back to back, can use up a session's allowance; when that happens the call fails after its retries, and once the limit resets `manimgen --resume` picks up from the cached plan and reuses sections that already rendered. `opus` uses the limits faster than `sonnet`. `MANIMGEN_MAX_RETRY_LLM_CALLS` caps the retry calls.

**With the API providers** you pay per token. At Gemini Flash pricing, a 5-section topic run costs roughly $0.02 to $0.05 and a 10-section PDF run $0.05 to $0.15. The Anthropic API costs more per run than Gemini Flash.

Set `tts.enabled: false` in `config.yaml` to skip narration during development.
