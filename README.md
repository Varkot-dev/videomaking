# ManimGen

An automated pipeline that converts a topic string or PDF of lecture notes into a narrated, animated CS explainer video in the style of 3Blue1Brown.

**Input:** `"binary search"` or `lecture.pdf`  
**Output:** A rendered `.mp4` with voiceover. Sections are planned at 20 to 45 seconds (topic, at most 6) or 30 to 45 seconds (PDF, at most 8), so a video is a few minutes long; the one real end-to-end run measured so far came out at 2:21.  
**LLM:** Claude through your Claude subscription by default (Claude Code in headless mode, no API key), with the Anthropic API, Gemini and Ollama as alternatives

How it is built and why: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

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
           │  up to 6 sections (topic) / 8 sections (PDF)
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
│     (token-free)                known ManimGL API mistakes │
│                                 auto-corrected; unknown    │
│                                 names blocked              │
│                                                            │
│  3. Safety gate ─────────────► scene_ast_gate: whole-tree  │
│     (security, token-free)      import allowlist + denylist│
│                                 hard block before render   │
│                                                            │
│  4. Timing verifier ─────────► static loop-aware timing    │
│     (token-free)                analysis; auto-fix or      │
│                                 route to retry             │
│                                                            │
│  5. Runner ──────────────────► subprocess: manimgl file.py │
│                                 1920×1080 @ 60fps, H.264   │
│                                                            │
│  6. Render validator ────────► frame_checker (PIL, free)   │
│     (two-tier)                  + text overlap probe (in   │
│                                 the render, free)          │
│                                 + layout_checker (LLM      │
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

Fixing these without a model call saves tokens and renders. What codeguard cannot fix deterministically goes to the LLM, along with any bare name that `from manimlib import *` does not provide (blocked before rendering; `MANIMGEN_UNKNOWN_SYMBOLS=report` turns that into logging only). `eval/results/fix_rule_inventory.md` has an itemised count of the rules.

### Timing verifier (`validator/timing_verifier.py`)

Statically analyses each generated scene before rendering to compute animation time per cue. Detects loop timing bugs and auto-corrects `self.wait()` values to fill the cue's exact narration duration. Runs at no LLM cost before every render attempt, so a timing problem is found before the render instead of at mux time.

### Error-aware retry (`validator/retry.py`)

When codeguard can't fix the code:
1. Classifies error type from stderr (`precheck_vgroup`, `syntax`, `import`, `attribute`, `type`, `runtime`, `timing`)
2. Generates targeted fix guidance for that error class
3. Sends `original_code + error + guidance` to the LLM for a targeted fix
4. Runs codeguard + timing verifier on result, then re-renders
5. Repeats up to `rendering.max_retries` attempts (3 in the shipped `config.yaml`) with configurable LLM call budgets (`MANIMGEN_MAX_RETRY_LLM_CALLS`, see the environment variable table). A render that never started (a draft refused by the checks) or a crash fixed by a token-free rule does not use up an attempt

---

## Audio-first CUE architecture

Narration audio drives animation timing — not the other way around.

1. TTS runs for all sections **before** any code is generated
2. `edge-tts WordBoundary` events give a start and end time for every spoken word
3. `segmenter.compute_segments()` converts word timestamps + `[CUE]` marker indices into exact per-cue durations
4. The Director receives these durations as hard constraints and writes `self.wait()` calls to match them
5. `muxer.py` pads (never speed-warps) — small mismatches from stream alignment are absorbed silently

---


## Project structure

```
videomaking/                      # git root (this README)
├── docs/                         # ARCHITECTURE.md, BRANCH_PROTECTION.md, roadmap/
├── .github/workflows/            # ci.yml, nightly-render.yml, secret-scan.yml
└── manimgen/                     # Python project root: run pip, pytest and manimgen from here
    ├── manimgen/                 # the importable package
    │   ├── cli.py                # entry point: manimgen <topic> | --pdf <file> | --resume
    │   ├── llm.py                # LLM client (claude_cli / anthropic / gemini / ollama)
    │   ├── input/ planner/ generator/   # PDF parsing, research and storyboard, the Director
    │   ├── validator/            # codeguard, safety gate, timing, render, retry, fallback
    │   ├── probes/               # text overlap probe that runs inside the render
    │   ├── renderer/             # TTS, audio slicing, muxing, assembly
    │   ├── editor/               # Flask clip editor (manimgen-edit)
    │   └── examples/             # hand-verified scenes shown to the Director
    ├── tests/                    # pytest suite, zero LLM, network or render calls
    ├── eval/                     # codeguard corpus, damage measurement, baseline
    ├── scripts/                  # env_doctor.py, render_smoke.py, tts_smoke.py, check_billing.py
    ├── docs/                     # KNOWN_ISSUES.md, LIBRARY_PC_FIRST_RUN.md
    ├── config.yaml               # LLM provider, model names, TTS config, render quality
    ├── requirements.txt          # runtime dependencies (setup.py reads this)
    ├── requirements-dev.txt      # requirements.txt + pytest, pytest-mock, hypothesis, ruff
    └── setup.py                  # console_scripts: manimgen, manimgen-edit
```

The module-by-module map, and why each piece is built the way it is, is in
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

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
sudo apt-get install ffmpeg pkg-config libpango1.0-dev libcairo2-dev
```

A LaTeX distribution is only needed for scenes that use `Tex()` formulas; `Text()` does not need one.

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

Everything below installs into your own user profile, so it should work on a locked-down machine such as a school or library PC. These steps follow the installers' documented options; they have not been run end to end on a locked-down PC, so treat the first run as the test and report the first step that fails. Use **PowerShell** (Start menu, type "PowerShell"). After each install that changes PATH, close and reopen PowerShell so it sees the change.

### 1. Python 3.11

1. Download the **Windows installer (64-bit)** for the latest Python 3.11 release from [python.org/downloads/windows](https://www.python.org/downloads/windows/). 3.11 is the version the Windows CI job installs with (`.github/workflows/ci.yml`), without a compiler; the project requires 3.11 or newer.
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

Only scenes that use `Tex()` formulas need LaTeX; `Text()` does not. Install [MiKTeX](https://miktex.org/download) and pick **"Install only for me"** (per-user, no admin). During setup set "Install missing packages on the fly" to **Yes**, so MiKTeX fetches packages when a formula first needs them (MiKTeX behavior; not tested with this project). Check: `latex --version`. The render environment finds `latex` through PATH on every OS.

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

- ManimGL renders with OpenGL 3.3 or newer. Whether the library PC's integrated Intel graphics can create that context has not been checked: run `python scripts\render_smoke.py` and read its `OpenGL` line. If it fails, the no-admin Mesa fallback in [manimgen/docs/LIBRARY_PC_FIRST_RUN.md](manimgen/docs/LIBRARY_PC_FIRST_RUN.md) is the tested route on GitHub's Windows machines. Render times on this class of machine are unmeasured; set `rendering.quality: l` or `m` in `config.yaml` for faster drafts.
- Narration needs internet access to Microsoft's speech service, which answered HTTP 403 to GitHub's machines (issue #109); whether it works from your network is unknown until `python scripts\tts_smoke.py` passes there. Without it, use `tts.enabled: false` (silent draft) or `--allow-silent`.
- Shared PCs that reset the user profile on logout will lose these installs; keep the clone and tools on a USB drive or expect to repeat the setup.

---

A step-by-step first-run checklist for a PC without admin rights is in [manimgen/docs/LIBRARY_PC_FIRST_RUN.md](manimgen/docs/LIBRARY_PC_FIRST_RUN.md).

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

# If narration (edge-tts) keeps failing, make those sections silent instead of stopping
manimgen --resume --allow-silent

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

Output: `manimgen/output/videos/<title>.mp4`, where the name is the plan's title in lower case with spaces as underscores.

### Environment variables

`manimgen --help` lists the command line flags. Everything else is configured in
`manimgen/config.yaml` or with these environment variables (set them in the
shell, or in a git-ignored `.env` file in `manimgen/`):

| Variable | Effect | Default |
|---|---|---|
| `LLM_PROVIDER` | `claude_cli`, `anthropic`, `gemini` or `ollama`; beats `llm_provider` in `config.yaml` | `claude_cli` |
| `ANTHROPIC_API_KEY`, `GEMINI_API_KEY` | Keys for the per-token providers only | unset |
| `MANIMGEN_ALLOW_PAID_API` | `1` allows the per-token providers (and calls Claude Code makes with an API key) | blocked |
| `MANIMGEN_MAX_PLAN_UTILIZATION` | Stop before a Claude plan window passes this share (0.01 to 1) when overage could bill | `0.90` |
| `MANIMGEN_MODEL_<ROLE>` | Model for one role, for example `MANIMGEN_MODEL_DIRECTOR=opus` | `llm.models` in `config.yaml`, then the provider default |
| `MANIMGEN_CONFIG` | Path of another `config.yaml` | the one in `manimgen/` |
| `MANIMGEN_MAX_RETRY_LLM_CALLS` | Seeds the two per-section repair budgets below | `rendering.max_retries` |
| `MANIMGEN_MAX_ERROR_LLM_CALLS`, `MANIMGEN_MAX_VISUAL_LLM_CALLS` | Per-section caps on error-fix and visual-fix LLM calls | the value above |
| `MANIMGEN_MAX_TOTAL_LLM_CALLS` | Per-run ceiling on repair LLM calls across all sections | `200` |
| `MANIMGEN_RENDER_TIMEOUT_2D`, `MANIMGEN_RENDER_TIMEOUT_3D`, `MANIMGEN_RENDER_TIMEOUT_FALLBACK` | Seconds a render may run before its process tree is killed | `rendering.render_timeout_*` |
| `MANIMGEN_FREEZE_BLOCK_THRESHOLD` | Seconds of dead screen at the end of a cue that block a render | `2.5` |
| `MANIMGEN_KWARG_ENFORCE` | `1` strips constructor keyword arguments proven invalid | off (log only) |
| `MANIMGEN_UNKNOWN_SYMBOLS` | `report` logs unknown ManimGL names instead of blocking the render | blocking |
| `MANIMGEN_OVERLAP_PROBE` | `0` turns off the text overlap probe | on |
| `MANIMGEN_FIRST_PASS_LAYOUT` | `1` runs the LLM vision layout check on the first render too | off |
| `MANIMGEN_RENDER_ENV_EXTRA` | Comma-separated extra variable names to pass to the render child | none |
| `MANIMGEN_EVIDENCE_LOG`, `MANIMGEN_EVIDENCE_DIR` | `0` disables the evidence log; folder it is written to | on, `output/logs` (relative to where you run) |
| `MANIMGEN_EDITOR_TOKEN` | Token for non-browser clients of the clip editor | random per start |
| `HTTPS_PROXY` | Proxy for edge-tts (or set `tts.proxy` in `config.yaml`) | unset |

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

`run_manifest.json` holds `title`, `topic_hash`, `result` (`ok`, `degraded`,
`usage_limit`, `narration_failed` or `no_video`), `exit_code`, `output`,
`stop_reason`, `started_at`, `seconds`, `sections` and `next_steps`. Each entry
of `sections` has `index`, `id`, `title`, `status`, `reason`, `clips` (how many
clips it contributed) and `seconds`.

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

Narration runs for every section before any scene is generated. edge-tts is
tried three times per section; if it still fails (no network, a proxy that
blocks it, the service down), the run stops right there with exit code 1, so
only the planning calls were used. Fix the connection (behind a proxy, set
`HTTPS_PROXY` or `tts.proxy` in `config.yaml`) and run `manimgen --resume`, or
run `manimgen --resume --allow-silent` to make the video with those sections
silent; they are then reported as `silent` and the exit code is 3.

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

Run pytest bare. `pyproject.toml` scopes it to `tests/` with `--strict-markers` and no `--ignore` flags, and CI runs the entire suite the same way. The LLM, network and render seams are mocked, so the suite costs nothing and needs no API key or Claude login. Two kinds of test run real tools: the media tests run ffmpeg on tiny generated clips (skipped when ffmpeg or ffprobe is missing), and one overlap test renders a real scene when manimgl and a display are available.

The suite covers:
- Every codeguard auto-fix and banned pattern
- Type-aware kwarg introspection (manimlib_signatures)
- Loop-aware timing analysis and auto-fix (timing_verifier)
- Scene safety gate (scene_ast_gate): attack corpus, every example passes, hard block on every render path
- Error-aware repair from real stderr tracebacks
- Section cap enforcement in the planner
- A/V sync contracts (muxer, slicer, segmenter)
- Frame defect detection (frame_checker)
- Text overlap probe geometry and its wiring into the repair loop (an opt-in test renders a real scene when manimgl and a display are available)
- PDF parser output structure and chunking logic
- The LLM provider switch, including how `claude -p` is invoked
- Documentation accuracy (`tests/test_docs_accuracy.py`: cited paths and scripts exist, no stale counts, exit codes, section caps and environment variable names match the code)

---

## Text overlap detection

Every render carries a small probe (`manimgen/probes/overlap_probe.py`) that
runs inside the `manimgl` process, where the position of every object is known.
At the end of each `play()` and `wait()` it compares the bounding boxes of the
visible text objects (`Text`, `MarkupText`, `Code`, `Tex`, `TexText`,
`DecimalNumber`, `Integer`) and reports any two that overlap by at least 20%
of the smaller one. It costs no LLM call and changes nothing in the scene file;
`run_manimgl` loads it through a `sitecustomize.py` on the child's
`PYTHONPATH`, which also works with the Windows `manimgl.exe` launcher.

A finding is a hard visual defect: the section goes through the existing
visual fix in the retry loop (same budgets, same repeat-defect stop), and if
the overlap survives the retries the section ships as
`accepted_with_defects` with the overlap named in the reason.

**See the report:** `<video>.overlaps.json` next to each rendered video.
**Switch it off:** `MANIMGEN_OVERLAP_PROBE=0`.

**Limits.** Text against text only: text over a shape, or shapes over each
other, is not checked. It looks at settled states, not mid-animation frames,
so a garbled `TransformMatchingTex` in progress is recorded in the report as a
score (`morphs`) but not acted on. The threshold was calibrated on one real run
and the bundled examples; the false-positive rate on other topics is not yet
measured.

---

## Security: generated code runs with your rights

The scene code is written by an LLM (the Director, the retry fixes) and run by
`manimgl` as you, with your normal user rights. The LLM reads the topic or PDF
you give it, so a document crafted to inject instructions could try to make it
write harmful code.

**What protects you today.** `validator/scene_ast_gate.py` parses every scene
(it never runs it) and walks the whole syntax tree, including method bodies,
nested functions, lambdas, comprehensions, decorators and default arguments.
It allows imports only from a short list of pure modules (`manimlib`, `numpy`,
`math`, `random`, `itertools`, `functools`, `typing`, `colorsys`,
`dataclasses`, `enum`, `collections` and a few more) and rejects `exec`,
`eval`, `compile`, `open`, `__import__`, `globals`/`locals`/`vars`,
`getattr`-style calls on computed names, dunder and private attribute access,
names such as `os`, `sys`, `subprocess` and `pickle` that `from manimlib import *`
leaks into scope, file, pickle and process methods (`.save`, `.load`,
`.system` ...), URL and network-path strings, TeX file and shell primitives,
and non-UTF-8 source encodings. A rejected scene is never rendered: the single
render entry point (`render_command.run_manimgl`) refuses it, a rejected Director
draft goes straight to the retry loop with the findings, and a rejected LLM fix
is thrown away.

**What it does not do.** It is a static denylist, **not a sandbox**. It stops
naive and moderately disguised payloads, but a determined author can still
reach something dangerous through a route no fixed list anticipates (for
example, building a config key or a URL from pieces at runtime). Once a scene
passes, it runs with your full rights. Codeguard is not a security check
either.

**Until stronger containment exists, use only topics and PDFs from sources you
trust.** The planned next step, a launcher that installs a Python audit hook to
deny network connections, unexpected processes and writes outside the output
folder, is deferred until untrusted PDFs need to be supported.

---

## Clip editor (`manimgen-edit`)

`manimgen-edit` starts a small Flask server on `127.0.0.1` (port 5001 unless
`--port` is given) and opens it in the browser. It lists the clips in
`output/muxed` (or `output/videos`), lets you trim and reorder them, and exports
one video into an `exports/` folder.

**What it protects against.** It binds to the loopback address only, and every
request whose `Host` header is not `localhost`, `127.0.0.1` or `::1` is refused
(this blocks DNS-rebinding pages). Requests that change anything must come from
the editor's own page (same origin) or carry the `X-Editor-Token` header; the
token is printed when the server starts. Clip names are checked to stay inside
the clips folder. An export never overwrites an earlier one (it takes the next
free name such as `title_2.mp4`), is written to a temporary file first so a failed
export leaves no partial file, and bad input (a body that is not a JSON object,
negative or non-numeric trim times, a trim end before its start) gets a 400 error
instead of an ffmpeg run.

**What it does not protect against.** It is a local tool, not a service. There is
no login: the same-origin check only stops web pages, and a program on the same
machine can send the headers it checks for. On a shared PC, another account that
can open `localhost:5001` can list your clips, play them and start exports, so
run the editor only when you are the one using the machine, and stop it when you
are done. Reading clips (GET requests) needs no token at all.

---

## Cost model

Each `manimgen` run makes these LLM calls, N being the number of sections:

- 1 research call and 1 lesson-planning call, plus 1 storyboard critic call
  (`_SELF_CORRECT_LIMIT` is 1). A planner reply with missing cue lists, or JSON
  that does not parse, can cost an extra call.
- 1 Director call per section.
- Repair calls, per section: up to `MANIMGEN_MAX_ERROR_LLM_CALLS` error fixes and
  `MANIMGEN_MAX_VISUAL_LLM_CALLS` visual fixes (both default to
  `rendering.max_retries`, 3 in the shipped config), and a whole run is capped by
  `MANIMGEN_MAX_TOTAL_LLM_CALLS` (200). Most sections should need none; no
  average has been measured.
- Vision checks: in the retry loop, one per attempt that finishes with no other
  defect found; on the first render only with `MANIMGEN_FIRST_PASS_LAYOUT=1`.

**With `claude_cli` (default)** there is no per-token bill. Each call counts against your Claude plan's usage limits, the same limits your normal Claude and Claude Code use draws from. A long PDF run, or many runs back to back, can use up a session's allowance; when that happens the run stops with exit code 4, and once the limit resets `manimgen --resume` picks up from the cached plan and reuses sections that already rendered. `opus` uses the limits faster than `sonnet`. `MANIMGEN_MAX_RETRY_LLM_CALLS` caps the retry calls. Every call is also written to `llm_usage.jsonl` in the logs folder (see [manimgen/README.md](manimgen/README.md)), so one video's share of the plan can be read back.

**With the API providers** you pay per token, and they are blocked unless `MANIMGEN_ALLOW_PAID_API=1`. No price per run has been measured for this project, so none is quoted here.

Set `tts.enabled: false` in `config.yaml` to skip narration during development.
