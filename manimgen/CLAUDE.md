# ManimGen — Session Guide

## ⚠️ Before running the pipeline: env-doctor
Recurring setup/version landmines (manimlib import, `setuptools<81` for
`pkg_resources`, manimgen editable path, broken `--fps`, planner `json_mode`)
are **actively guarded**. The `.claude/settings.json` SessionStart hook runs
`python3 scripts/env_doctor.py` automatically; run it manually anytime with:

```bash
python3 scripts/env_doctor.py   # exit 1 = blocking issue, prints the exact fix
```

Full diagnoses + fixes: **`docs/KNOWN_ISSUES.md`**. When you find a NEW recurring
breakage: fix it, add a check to `scripts/env_doctor.py`, document it in
`docs/KNOWN_ISSUES.md`. Enforcement beats memory.

## What this project is
Automated pipeline: **topic string or PDF → 3Blue1Brown-style animated explainer video with narration.**
Uses an audio-first CUE pipeline where spoken word timestamps drive animation durations — no speed warping.

**Stack:** Python 3.11+ (CI uses 3.13), ManimGL 1.7.2 (3b1b fork), Claude via Claude Code `claude -p` (default LLM provider), FFmpeg, LaTeX, edge-tts, Flask (editor)

**Platforms:** macOS, Linux and Windows. The user's own machine is a Windows 11 PC without admin rights, so keep every code path cross-platform (`os.pathsep`, `shutil.which`, no POSIX-only commands) and keep the no-admin Windows setup in the root README.md working.

**Repo:** `https://github.com/Varkot-dev/videomaking.git` — branch `main`

**Where things are documented:** setup, usage, exit codes and environment variables are in `../README.md`; the module map and the reasons behind each design decision are in `../docs/ARCHITECTURE.md`; what is broken, unverified or not done is in `docs/KNOWN_ISSUES.md`.

**Tests:** run `python3 -m pytest -q` — bare, no flags. `pyproject.toml` is the
single source of truth for how the suite runs, and it deliberately carries no
default `--ignore`. Never add `--ignore=tests/test_scene_generator.py`
(or `test_planner.py` / `test_pipeline_e2e.py`): those files are fully mocked
and cost nothing, so skipping them only hides local↔CI divergence. Do not
transcribe the pass count into any doc — it goes stale within a day; run the
command instead. `tests/test_docs_accuracy.py` enforces this.

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
│   ├── llm.py                   # shared LLM client (claude_cli/anthropic/gemini/ollama, config-driven)
│   ├── utils.py                 # shared: strip_fencing(), section_class_name()
│   ├── input/
│   │   ├── parser.py            # normalize topic string
│   │   └── pdf_parser.py        # extract text + render pages from PDF
│   ├── techniques.py            # the one technique registry (planner, critic, 3D promotion, example tags)
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
│   │   ├── timing_verifier.py   # loop-aware static timing analysis + auto-fix (wired via retry.apply_timing_gate)
│   │   ├── env.py               # render environment vars (cross-platform PATH, LaTeX lookup)
│   │   └── prompts/             # retry_system.md, fallback_system.md, layout_checker_system.md
│   ├── probes/                  # code that runs INSIDE the manimgl child (see "Visual validation")
│   │   ├── overlap_probe.py     # text-over-text detector, stdlib only, loaded by path
│   │   ├── overlap_report.py    # host side: child env (PYTHONPATH, report path), report parsing
│   │   └── bootstrap/sitecustomize.py  # start-up hook; chains any shadowed sitecustomize
│   ├── renderer/
│   │   ├── tts.py               # edge-tts with WordBoundary → per-word timestamps; engine dispatch and offline fallback
│   │   ├── tts_kokoro.py        # optional offline voice (kokoro-onnx), model download, estimated word timings
│   │   ├── audio_slicer.py      # full audio → N cue-aligned .m4a slices (AAC, sample-accurate)
│   │   ├── cutter.py            # cut rendered section .mp4 into per-cue clips (parallel FFmpeg)
│   │   ├── muxer.py             # audio+video mux (pad-only, no speed warp)
│   │   └── assembler.py         # normalize 1920x1080@60fps, xfade between sections
│   ├── examples/                # hand-written verified ManimGL scenes (Director few-shot reference),
│   │                            # shipped as package data. Each scene has `techniques: <name>, <name>`
│   │                            # as first line of its class docstring (names from techniques.py)
│   └── editor/
│       ├── server.py            # Flask UI for clip review, reorder, trim, export
│       └── templates/editor.html
├── tests/                       # pytest suite (run `python3 -m pytest -q`)
├── config.yaml                  # LLM provider, model names, TTS config, render quality
├── requirements.txt             # runtime deps (setup.py reads it for install_requires)
├── requirements-dev.txt         # -r requirements.txt + pytest, pytest-mock, pytest-timeout, pytest-cov, hypothesis, ruff==0.15.13
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
  1. TTS (edge-tts WordBoundary)  → full .mp3 + per-word timestamps
  2. segmenter.compute_segments() → cue onsets; slice_durations() → per-cue length (onset to next onset)
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
- **Content-keyed cache with hash sidecars.** Each rendered video and each muxed cue clip has a `.hash` sidecar storing the section's content key (`cli._section_key`: the section dict, topic or PDF-bytes hash, TTS voice and speed, cue durations rounded to 0.05s). Files are shared across plans and named only by section id, so a file whose sidecar is missing, empty or different is rebuilt; same content (`--resume`) is reused. Audio slices are always re-sliced.
- **Sections run in parallel.** After narration, sections are generated and rendered `rendering.parallel_sections` at a time (default 3; `MANIMGEN_PARALLEL_SECTIONS=1` for one at a time). Clips are assembled in plan order regardless of which section finishes first.
- **Speed flags.** `--fast` skips the research brief and the storyboard critic pass (`lesson_planner.set_fast`); `--draft` renders small, 720p or 540x960 for a short (`paths.set_draft`). Both are part of the cache key, so a draft never reuses or overwrites a final render.
- **Retry loop always reloads code.** After each fix attempt (codeguard or LLM), the file is always re-read from disk — codeguard may have applied in-place fixes that would otherwise be discarded.

---

## When to use the LLM vs hardcode

**Hardcode (no API calls) when:**
- Testing ManimGL rendering behaviour — camera, surfaces, animations, depth, opacity
- Verifying a new 3D API works before adding it to the pipeline
- Writing example scenes for the Director's few-shot reference (`manimgen/examples/`)

**Use the LLM when:**
- Testing Director *output quality* — does it generate visually correct code for a given storyboard?
- Running the full pipeline end-to-end on a real topic or PDF

**Rule:** if you're not evaluating LLM output quality, hardcode. Writing scenes manually is faster, free, and gives exact control over what's being tested.

---

## Running the pipeline

```bash
# From manimgen/ project root. Default provider (claude_cli) needs no API key,
# only Claude Code on PATH and logged in (run `claude` once).
MANIMGEN_MAX_RETRY_LLM_CALLS=2 manimgen "gradient descent"
manimgen --pdf notes.pdf
manimgen --resume   # resume from cached plan.json

# Another provider for one run
# (gemini and anthropic bill per token and are blocked without MANIMGEN_ALLOW_PAID_API=1)
MANIMGEN_ALLOW_PAID_API=1 LLM_PROVIDER=gemini GEMINI_API_KEY=<key> manimgen "gradient descent"

manimgen-edit   # launch clip editor

# Run tests (zero cost, everything LLM/subprocess is mocked)
python3 -m pytest -q
```

**API keys** (only for the `anthropic` / `gemini` providers): `manimgen/.env`
(`GEMINI_API_KEY=...`, `ANTHROPIC_API_KEY=...`), git-ignored, loaded by `llm.py`.

**Config and output locations:** `manimgen/config.py` is the one loader of `config.yaml` (or the file named by `MANIMGEN_CONFIG`); a missing or malformed file raises `ConfigError`, never a silent default. Relative `output:` paths resolve against the folder holding config.yaml, not the cwd. Editable install (`pip install -e .`) only.

**Output locations** (relative to the config.yaml folder):
- Final video: `manimgen/output/videos/<title>.mp4`
- Muxed clips: `manimgen/output/muxed/`
- Scene code + logs: `manimgen/output/scenes/`, `manimgen/output/logs/`

---

## Vertical shorts mode (`--short`)

`manimgen "limits" --short --format misconception --confused "..." --clicked "..."` makes one idea, 30 to 60 seconds, one section, rendered 1080x1920 for YouTube Shorts.

- **Format switch:** `paths.set_video_format("short")` is called once by `cli.py` (from `--short`, or from `_format: "short"` in a resumed plan). Everything else reads `paths.is_short()`: `render_size_flags()` passes `-r 1080x1920` and no quality flag (manimgl's `-l/-m/--hd` are landscape presets), `render_resolution()` returns `1080x1920` so the assembler and muxer follow, and the fallback card uses its vertical template.
- **Frame:** manimgl derives the frame from the resolution, so it is 4.5 units wide and 8 tall (verified by a real render). Readable content stays in x -2.0..1.4, y -2.0..2.8, centred on x = -0.3.
- **Planner:** `lesson_planner.plan_short()` with `planner/prompts/planner_short_system.md`. Exactly one section; `--format` picks `puzzle`, `misconception`, `proof` or `worked`; `--confused` / `--clicked` carry the owner's angle; the owner's voice profile (a git-ignored copy of `voice_profile.example.md`, path from `shorts.voice_profile` in config.yaml) carries how they talk.
- **Director:** `generator/prompts/director_vertical.md` is appended to the Director prompt and overrides the landscape layout numbers. It embeds one verified vertical reference scene; a test checks that scene against the safety gate, codeguard and the timing verifier. Landscape few-shot examples and reference frames are not sent for a short.
- **Font sizes:** codeguard snaps to the canonical scale, so shorts use 28 (hook), 22 (labels), 36 (key line).
- **Placement:** the prompt pins the hook's top edge at y 2.75 and the key line's at 1.9 (`move_to(..., aligned_edge=UP)`), and labels under the diagram go into `main` before it is scaled. During the render, the probe also moves new text-only mobjects that sit up to 0.5 units outside the safe area back inside before their first frame (`MANIMGEN_SAFE_NUDGE=0` turns it off; the count is `nudges` in the probe report). Anything further out is still reported.
- **Speed:** the director runs on `low` effort (`llm.efforts` in config.yaml). The retry loop checks a rejected first render instead of rendering the same code again, and allows one visual fix for text-placement defects (`MANIMGEN_MAX_PLACEMENT_FIXES`) before shipping the best render. Measured on 12 runs: most of a 15 to 30 minute run was re-renders for placement defects that never cleared.
- **Captions (`--captions`, `shorts.captions.enabled`):** `renderer/captions.py`. Off by default; `cli.py` ignores the flag with a warning unless `--short`. `chunk_words()` turns the narration's word timestamps (the same ones `segmenter.py` uses; no speech recognition) into 1 to 4 word chunks, broken at punctuation (recovered from the narration text, since WordBoundary tokens carry none), at pauses of 0.35s or more and at 26 characters; each shows from its first word's start to its last word's end, and gaps under 0.3s are bridged. `section_offsets()` places each section on the final timeline using the assembler's crossfade plan (a Short has one section, so 0). `build_ass()` writes a 1080x1920 ASS file (bold, white, dark outline, bottom centre, margins L 48 / R 192 / V `shorts.captions.margin_bottom_px`, default 488, just above the 480 px Shorts UI band) and `burn_captions()` burns it with ffmpeg's `subtitles` filter in one encode, replacing the video in place via a temp file; with no libass (`ffmpeg -filters`) it falls back to one `drawtext` per chunk, and with neither it draws each chunk as a PIL PNG and burns them with `overlay` filters (any ffmpeg), same position and margins. ASS paths go through `escape_filter_path()` (`C\:/path` on Windows). It runs from `cli._add_captions` as the last step on the freshly assembled video, so it needs no cache key. No word timestamps (TTS off, silent section) skips with a log line, and a failed burn leaves the uncaptioned video. The caption band is a fixed screen position: the separate layout effort should reserve that band (y about 1320 to 1440 px from the top) so the diagram never sits under the text.
- **Two drafts (`shorts.drafts`, `MANIMGEN_SHORT_DRAFTS`):** default 2, `1` disables, capped at 3; shorts only. `cli._generate_and_gate` hands over to `generator/scene_drafts.pick_best_draft`, which runs the Director once per draft in parallel threads (each is its own `claude -p` call). Every draft goes through the same steps as a single scene (`generate_scenes(variant=...)`: codeguard, safety gate; then the timing gate) in its own file (section id plus `_draftA`) with its own class name (`utils.draft_class_name`, which must not contain the final class name because `_find_rendered_video` falls back to substring matches). Surviving drafts render in parallel at 540x960 through `run_manimgl(..., draft=True)`, a per-call size (`paths.render_size_flags(draft=True)`) that never touches the process-wide `paths.set_draft`, with a short budget (`render_command.draft_scene_timeout`, at most the normal budget). Score, lower is better: failed (rejected before rendering, render failure or timeout, writing error) is worst; then hard issues (every probe finding, blocking freezes, hard frame checks); then timing warnings; then shorter code; ties go to the first draft. The winner is copied to the section's normal scene file with the normal class name and the pipeline continues unchanged (full-size render, retry, fallback); draft videos are deleted and are never the final. If no draft can be judged, draft A goes on unchanged so retry and fallback still apply. A usage-limit stop in any draft stops the run. Drafts are written only when codegen runs, so `--resume` with an up-to-date render does not write them, and `_section_key` is unchanged. Scores: run log line `[drafts] section_01: A score=2/0/2140, B score=0/1/1980, picked B (score is hard issues/timing warnings/code chars)` (a failed draft reads `score=failed (reason)`) and one JSON line per section in `logs/drafts.jsonl`. Unmeasured: how often the second draft wins, and how much plan allowance it costs per short.
- **Publishing:** the `manimgen-shorts-publish` skill (review gate, sameness check, title and description, upload sheet).
- **Not built yet:** own-voice recording, music. (Text off-screen or under the Shorts UI is now reported by the render probe.)
- **Research behind these choices:** `../docs/research/educational-shorts-playbook.md` (sources and evidence labels in `../docs/research/shorts-notes/`).

## Benchmark (`bench/`, code in `manimgen/bench/`)

A fixed set of 10 YouTube Short topics (`bench/topics.yaml`) and a scorecard, so a pipeline change is judged on the same inputs with numbers. Run from `manimgen/`:

```bash
python -m manimgen.bench run --variant baseline                  # plan once per topic (cached), render, score
python -m manimgen.bench run --only pythag,odds --variant low --env MANIMGEN_MAX_PLACEMENT_FIXES=0
python -m manimgen.bench score baseline low                      # per-topic table, totals, side-by-side comparison
python -m manimgen.bench sheet baseline                          # frames.png: 4 frames per video, one row per topic
```

`run` plans each topic once in fast mode and reuses that plan for every variant, with TTS off. Results go to `bench/runs/<variant>/<slug>/` (git-ignored), with a markdown and a JSON scorecard per variant. The scorer reports wall time, director seconds and output tokens, other LLM seconds, outcome (`ok`, `accepted_with_defects`, `fallback`), first-try pass, number of renders, and probe findings by kind. `--dir <exp>` reads an older `<exp>/<slug>/<variant>/` tree. A run spends Claude plan usage, so launch it on purpose.

---

## LLM provider toggle

Resolution order (first wins):
1. `LLM_PROVIDER` env var
2. `llm_provider` in `config.yaml` (currently `claude_cli`)
3. Default: `"claude_cli"`

Model names and `max_tokens` are configured under `llm:` in `config.yaml`, never hardcoded.

| Provider | Model (config key) | Mechanism | Billing |
|---|---|---|---|
| `claude_cli` (default) | `sonnet` or `opus` (`llm.claude_cli_model`) | `claude -p` subprocess (Claude Code headless) | User's Claude plan usage limits, no API key |
| `anthropic` | `claude-sonnet-5-5` (`llm.anthropic_model`), `max_tokens` 16000 | `anthropic` SDK | Per token, `ANTHROPIC_API_KEY` |
| `gemini` | `gemini-2.5-flash` (`llm.gemini_model`) | `google.genai` (NOT the deprecated `google.generativeai`) | Per token, `GEMINI_API_KEY` |
| `ollama` | `llama3.1` (`llm.ollama_model`) | local HTTP, loopback, private and link-local URLs only (SSRF guard) | Free |

**`claude_cli` details (`llm._claude_cli`):**
- Needs Claude Code installed and logged in (`claude` once). `llm.claude_cli_path` overrides the executable name or gives a full path; `scripts/env_doctor.py` warns when it is not on PATH.
- System prompt goes through `--system-prompt-file` in a temp dir; the user turn and any base64 PNG frames go on stdin as one `stream-json` message. Nothing large is put on argv (Windows caps a command line at ~32K chars).
- Flags: `--tools=` (single token, so it survives the Windows cmd shim), `--strict-mcp-config`, `--disable-slash-commands`, `--no-session-persistence`, `--model <model for the role>` (`llm.claude_cli_model` unless `llm.models` or `MANIMGEN_MODEL_<ROLE>` says otherwise). cwd is an empty temp dir.
- Env: built from an allowlist of variable names (`_CLI_ENV_ALLOWLIST`), so other services' keys never reach the child. `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, the Bedrock/Vertex/Foundry switches and a parent Claude Code session's identity are removed on purpose (otherwise Claude Code would bill the API instead of the plan), and `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1` keeps CLAUDE.md files out of the prompt.
- 600s timeout per call, 3 attempts (not retried when the error says not logged in, usage limit or credit balance); the final `{"type": "result"}` stream event is the reply. Guards: `_ensure_subscription_auth`, `_check_plan_limits`, `_ensure_plan_headroom` (`MANIMGEN_MAX_PLAN_UTILIZATION`, default 0.90).
- `json_mode=True` is native only on Gemini. For `claude_cli`, `anthropic` and `ollama` the prompt asks for JSON and `chat()` strips a surrounding markdown `json` code fence.

---

## Video quality systems (current)

### 1. Prompt architecture
- `planner/prompts/researcher_system.md`: Panel of Experts prompt — simulates professor, pedagogy expert, and explainer creator. Returns rich JSON brief with historical context, textbook vs intuition, multiple perspectives, misconceptions, visual opportunities.
- `planner/prompts/planner_system.md`: outputs storyboard with `cues[{index, visual}]`. Each `visual` gives exact ManimGL-implementable descriptions.
- `generator/prompts/director_system.md`: ManimGL API reference, layout zone rules, banned patterns, technique table.
- `manimgen/examples/`: hand-written verified ManimGL scenes. Each has a `techniques:` tag in its docstring — `scene_generator.py` reads this tag to select relevant examples per section automatically.

### 2. Dark background
All `manimgl` subprocess calls use `-c "#1C1C1C"`. The flag is `-c`, NOT `--background_color`.

### 3. Video quality
- Assembler uses `-preset slow -crf 17` (near-lossless). Previously `veryfast` caused blurry output.
- Output normalized to `1920x1080@60fps` (from `rendering.resolution` / `rendering.fps` in `config.yaml`).
- Subprocess timeouts are **per-call and sized to the work**, not one global
  value. Current tiers: ffprobe/audio probes 5–15s, short ffmpeg and
  frame-extract calls 30s, audio slicing 120s, full ffmpeg encodes 300s,
  `manimgl` scene renders get `max(rendering.render_timeout_*, 60s + 10s per second of video)` (config.yaml; the formula is skipped when `MANIMGEN_RENDER_TIMEOUT_*` is set; `_scene_timeout` in `validator/render_command.py`), whole process tree killed on expiry. Values live in named
  `_*_TIMEOUT_SECONDS` constants near the top of each renderer module — read
  the constant rather than trusting this list. When adding a new subprocess
  call, always pass an explicit `timeout=`: an ffmpeg or ffprobe call without
  one can hang the entire pipeline indefinitely.

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
| `self.play(obj.become(...), ...)` | → `self.play(obj.animate.become(...), ...)` (other args kept) |
| `self.play(SurroundingRectangle(...))` | wrap in `ShowCreation()` |
| `Tex(r"\text{label}")` outer wrapper | strip to `Tex(r"label")` |
| `font_size=` on `Tex()` | left alone (valid param, handled internally) |
| negative/zero `self.wait()` | clamped to `0.01` |
| VGroup item assignment | banned pattern (not auto-fixable) |
| `FadeOut(a, b)` | → `FadeOut(a), FadeOut(b)` |

### 5. Retry loop
Order: codeguard auto-fix → error-aware fix (token-free) → LLM fix (budget-capped) → visual fix (if layout checker finds defects) → fallback.

After every fix, the file is **always reloaded from disk** — previously a bug caused the LLM fix to be silently discarded.

`SceneErrorType` enum classifies errors for targeted guidance: `PRECHECK_VGROUP`, `SYNTAX`, `IMPORT`, `ATTRIBUTE`, `TYPE`, `RUNTIME`, `TIMING`, `SAFETY_GATE`, `TIMEOUT`.

### 6. Visual validation (two-tier)
- **Tier 1 (frame_checker.py):** zero-cost PIL-based — detects black frames, frozen frames, edge clipping.
- **Tier 1b (probes/overlap_probe.py, #97):** zero-cost text-over-text check run inside the manimgl render. `render_command.run_manimgl` prepends `probes/bootstrap/` to the child's PYTHONPATH (joined with `os.pathsep`) and sets `MANIMGEN_OVERLAP_REPORT`; the bootstrap `sitecustomize.py` installs a `sys.meta_path` hook that wraps `Scene.post_play`/`tear_down` when manimgl imports `manimlib.scene.scene` (the scene file is untouched, so the safety gate is unchanged). After every `play()`/`wait()` it compares bounding boxes of visible text-like mobjects (StringMobject, DecimalNumber, SingleStringTex; not Brace) and reports pairs overlapping >= 20% of the smaller box (ancestor, same-string and < 0.02 unit² overlaps ignored; max 10 findings, 3 per string). Findings land in `RenderResult.overlaps` and `<video>.overlaps.json`, become hard `OVERLAP:` issues in `validate_render` and `retry_scene` (normal visual-fix budget and signature dedup), and survivors make the section `accepted_with_defects`. A probe failure is recorded as `probe_error` and never fails a render. Off switch: `MANIMGEN_OVERLAP_PROBE=0`. Limits: text vs text only (not text vs shapes); stable states only (TransformMatchingTex midpoint garble is scored in the report's `morphs` list but not acted on); calibration record and the two real offending scenes in `tests/fixtures/overlap/`; false-positive rate on other topics unmeasured; for an `always_redraw` text the reported string can be stale (`become()` does not copy it), the geometry is current.
- **Tier 2 (layout_checker.py):** LLM vision, multi-frame sampling at 25%/50%/75% of duration, returns structured `ISSUE | CAUSE | FIX` feedback (only `ISSUE:` lines count; any other non-OK reply is UNVERIFIED). It runs in `retry_scene`, NOT on the first pass: `validate_render` skips it unless `MANIMGEN_FIRST_PASS_LAYOUT=1`, because its verdict was never enforced there.

### 7. TTS voice
- Engine: `edge-tts` (free, needs Microsoft's online service), chosen by `tts.engine`
- Voice: `en-US-AndrewMultilingualNeural`
- Speed: `+5%`
- Word timestamps via `WordBoundary` mode for CUE alignment
- **Offline engine** (`renderer/tts_kokoro.py`, optional `requirements-voice.txt`): Kokoro through `kokoro-onnx`. `tts.generate_narration` dispatches on `tts.engine` (`edge-tts` | `kokoro`). With `tts.fallback_engine: kokoro` (default) and the package importable, an edge-tts network or service failure after its three attempts logs one line and switches the rest of the run to Kokoro (`tts._edge_unreachable`, so one video does not mix voices). Without the package, behaviour is unchanged.
- **Timings are built, not measured:** each cue is synthesized separately and the clips are joined with a gap, so cue onsets are exact; inside a cue, phrases (split at punctuation) are exact too, and each word gets a share of its phrase proportional to its phoneme count (estimate). Timestamps are one per `str.split()` token, so `align_cue_indices` is a no-op. Output is an `.mp3` (48 kHz mono) at the same path edge-tts writes; the slicer and muxer are untouched. The cue word indices reach the engine through `tts.cue_hint(...)` (set in `cli._run_tts_for_section`), so `generate_narration(text, path)` keeps its two-argument call.
- **Model files** (`kokoro-v1.0.int8.onnx`, `voices-v1.0.bin`, about 120 MB) download on first use into a per-user cache folder (size-checked, atomic rename); `MANIMGEN_KOKORO_DIR` or `tts.kokoro.model_dir` override it. Never put them in the repo. `tts.kokoro.voice` (default `am_michael`) and `tts.kokoro.speed` (default 1.05).
- **Cache key:** `cli._content_hash` adds the Kokoro voice and speed when `tts.engine` is kokoro; `cli._section_key` adds `tts.engine_tag()` whenever Kokoro is the voice in effect, including after a fallback. edge-tts keys are unchanged.
- **Tests:** `tests/conftest.py` forces `tts_kokoro.is_installed()` to False in every test so an installed package never changes unrelated tests; `tests/test_tts_kokoro.py` has one real-synthesis test that runs only when the package and model files are present (point `MANIMGEN_KOKORO_DIR` at them).

### 8. Muxer
- Cue lengths (`cli._cue_durations` = `segmenter.slice_durations`) are the audio slice lengths, onset to next onset. They sum to the narration length. Never use `seg.duration` (ends at the last word's `.end`) for video: it is shorter by the pause between cues and every cue then ends in a frozen frame (the 0.4 s Shorts freeze).
- The cutter rounds cue edges to the fps grid from absolute time (`muxer.cue_frame_span`) and cuts by frame count, so sub-frame remainders carry into the next cue and clips sum to the section length within half a frame. Per-cue files are kept for the editor.
- Audio longer than video by more than 2 frames → freeze last frame (`tpad`, gap only); by less (frame rounding, AAC padding) → audio is trimmed to the video
- Video longer than audio → pad audio with silence
- Logs WARNING at diff > 1.0s, LARGE MISMATCH at diff > 1.5s

---

## Security: generated scene code
LLM-written scenes run under `manimgl` with the user's full rights.
`validator/scene_ast_gate.py` is a whole-tree static gate (import allowlist plus
denylist of dangerous names, attributes, strings and encodings). It is a HARD
block: `render_command.run_manimgl` refuses a rejected file, the generator routes
a rejected draft to retry, and retry discards a rejected LLM fix. It is NOT a
sandbox and can be bypassed by a determined author; only trusted topics and PDFs
should be used until the audit-hook launcher (deferred, issue #87 option 2)
exists. Every file in `manimgen/examples/` must pass the gate
(`tests/test_scene_gate_attacks.py`). When a legit scene is rejected, widen the
gate narrowly and add a test; never route a render around `run_manimgl`.

---

## Key rules for development

1. **Never use `--background_color`** — the correct flag is `-c "#1C1C1C"`
2. **`Tex()` accepts `font_size=`** — valid param, don't convert to `.scale()` (double-scales)
3. **All prompts live as `.md` files** in `prompts/` dirs — never inline Python strings
4. **Edit `manimgen/manimgen/`** — that's the importable package, not the top-level `manimgen/`
5. **codeguard is the first line of defense** — extend it for any new known-bad pattern before touching prompts
6. **No duplicate source files** — never create top-level mirrors of source files
7. **Adding a new example scene:** add to `manimgen/examples/`, add `techniques: <name>` as first docstring line (the name must be in `manimgen/techniques.py`; a test enforces it). No other code changes needed.
8. **Core design principles**: no hardcoded mappings, no duplicate sources of truth, no speculative abstractions. (The longer engineering guide is `MASTER GUIDELINES.md` at the git root.)
9. **Docs must match code.** `tests/test_docs_accuracy.py` mechanically checks that every relative path the docs cite exists, that no doc hardcodes a test count, that no tracked doc leaks a personal home-directory absolute path, that every `MANIMGEN_*` variable a doc names appears in the code, that the exit codes and section caps in the docs match `cli.py` and `lesson_planner.py`, and that every script a doc tells you to run exists. If it fails, fix the doc, never weaken the test.

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

## Fixed bugs log (why the code is this way)

- **ManimCommunity camera calls on 2D scenes.** The Director wrote `self.set_camera_orientation(...)`, `self.frame.reorient(theta_deg=..., phi_deg=...)` and `NumberLine(..., label=...)`, each crashing every render. `codeguard.py` now rewrites or strips all three (`_fix_set_camera_orientation`, `_fix_reorient_wrong_kwargs`, `_strip_label_kwarg_from_numberline`), they are banned patterns, and the director prompt lists them under "Never use".
- **Stale render cache.** `_render_is_fresh()` used a hardcoded path that did not match ManimGL's output; it now locates the video with `_find_rendered_video(class_name)` first.
- **Retry loop silent discard.** An LLM fix was only reloaded from disk when codeguard also patched something. The file is now always reloaded after every fix attempt.
- **A/V sync.** Cue 0 silence (segmenter measures from 0.0); last-syllable clipping (uses `word.end`, not the next word's start); MP3 stream-copy drift (re-encode to AAC); `tpad` duration (uses the gap, not total audio); sample-rate drift (`-ar 48000`).
- **Cue index off-by-one.** The LLM is told `cues[]` needs N+1 entries (N `[CUE]` markers plus the opening segment at index 0).
- **VGroup swap pattern.** `_fix_become_inside_play()`, `set_fill_color` rewritten to `set_fill`, VGroup assignment banned.
- **Blurry output.** The assembler uses `-preset slow -crf 17`, not `veryfast`.
- **Timing gate.** `timing_verifier` runs as one gate (`retry.apply_timing_gate`) before the first render in `cli.py`, on the initial code in `retry_scene`, and after every fix. Unresolvable timing issues skip the first render and go to the retry path with the warnings in the fix prompt. The director prompt now opens its cue-timing section with the loop-accumulator rule.
- **Frame checker wiring.** `check_frames()` runs after every successful render in `validator/retry.py`; hard failures block muxing and force a retry; layout issues are advisory on the first pass (`MANIMGEN_FIRST_PASS_LAYOUT=1`) and feed the fix prompt in `retry_scene`.
- **Config parsing.** `llm.py` reads `config.yaml` once at import into `_LLM_CONFIG`, not on every call. `.hypothesis/` and `.DS_Store` are in `.gitignore`.

---

## Known issues / next steps

Open problems, measured limits and unverified claims are kept in one place:
`docs/KNOWN_ISSUES.md` (see "Not done, not verified"). Do not list them here.

---

## Testing (zero cost)

```bash
# Full suite. Bare invocation — pyproject.toml already scopes it to tests/.
# There are no "LLM-calling tests" to skip: test_scene_generator.py,
# test_planner.py and test_pipeline_e2e.py mock every LLM seam and cost nothing.
python3 -m pytest -q

# Specific areas
python3 -m pytest tests/test_codeguard.py -v          # all codeguard fixes + bans
python3 -m pytest tests/test_pipeline_contracts.py -v  # A/V sync contracts
python3 -m pytest tests/test_research_step.py -v       # researcher + planner
python3 -m pytest tests/test_frame_checker.py -v       # zero-cost visual checks
python3 -m pytest tests/test_timing_verifier.py -v     # static timing analysis
python3 -m pytest tests/test_llm.py -v                 # provider switch, claude -p invocation

# Lint, same pinned version as CI (installed by requirements-dev.txt)
ruff check manimgen/ && ruff format --check manimgen/
```

---

## A/V sync — fixed contracts (test_pipeline_contracts.py)

1. Cue 0 measured from `0.0` — pre-speech silence preserved
2. Segment boundary uses `word.end` — last syllable never clipped
3. Slicer re-encodes to AAC — no ~26ms MP3 frame-boundary drift
4. Muxer `tpad` uses gap — not total audio duration
5. Assembler enforces `-ar 48000` in all three re-encode paths
