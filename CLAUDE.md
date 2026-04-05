# ManimGen — Claude Code Guide

## What this project is
Automated pipeline: topic string → 3Blue1Brown-style animated explainer video.
Full pipeline runs end-to-end as of 2026-04-05.

**Stack:** Python 3.9+, ManimGL (3b1b), Gemini API (dev) / Claude API (prod), FFmpeg, LaTeX, edge-tts

---

## Critical: ManimGL vs ManimCommunity
This project uses `manimgl` (3b1b's version), **NOT** `manim` (ManimCommunity).

| Correct (ManimGL) | Wrong (ManimCommunity) |
|---|---|
| `from manimlib import *` | `from manim import *` |
| `ShowCreation(obj)` | `Create(obj)` |
| `Tex(...)` | `MathTex(...)` |
| `self.frame` | `self.camera.frame` |
| `manimgl file.py ClassName -w --hd` | `manim file.py ClassName` |

Mixing the two APIs is the #1 source of bugs. `codeguard.py` auto-fixes many of these.

---

## Project structure

```
manimgen/
├── manimgen/               # source package
│   ├── cli.py              # entry point: manimgen <topic>
│   ├── llm.py              # shared LLM client (Gemini/Anthropic toggle)
│   ├── input/parser.py     # normalize topic string
│   ├── planner/
│   │   ├── lesson_planner.py   # LLM → structured lesson plan JSON
│   │   └── prompts/planner_system.md
│   ├── generator/
│   │   ├── scene_generator.py  # LLM → ManimGL scene code
│   │   └── prompts/            # generator_system.md, rules_core.md
│   ├── validator/
│   │   ├── codeguard.py        # static checks + auto-fixes (token-free)
│   │   ├── runner.py           # subprocess manimgl, logs attempt
│   │   ├── retry.py            # retry loop (up to 3 LLM fix attempts)
│   │   ├── fallback.py         # title-text fallback scene
│   │   └── env.py              # render environment vars
│   └── renderer/
│       ├── assembler.py        # FFmpeg concat of section videos
│       └── tts.py              # STUB — TTS not implemented yet
├── examples/               # 5 hand-written verified ManimGL scenes (few-shot seeds)
├── manimgen/output/
│   ├── scenes/             # generated .py files (one per section)
│   ├── videos/             # final assembled video + individual section videos
│   └── logs/               # timestamped runner logs + attempt artifacts
├── config.yaml             # LLM provider, output dirs, render quality, TTS engine
└── requirements.txt
```

---

## Pipeline flow

```
topic string
  → parse_input()           normalize
  → plan_lesson()           LLM → {title, sections: [{id, title, visual_description, ...}]}
  → for each section:
      generate_scenes()     LLM → ManimGL .py file
      run_scene()           manimgl subprocess → video, or fail
      retry_scene()         up to 3 LLM fix attempts (codeguard auto-fixes first)
      fallback_scene()      title-text scene if all retries fail
  → assemble_video()        FFmpeg concat → final .mp4
```

---

## Running the pipeline

```bash
# from manimgen/ project root
export GEMINI_API_KEY=...      # or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic
manimgen "binary search"
```

Output lands in `manimgen/output/videos/<title>.mp4`.
Individual section videos are in `videos/` (manimgl default output dir).

---

## LLM provider toggle

Set via env var or `config.yaml`:
- `LLM_PROVIDER=gemini` (default) — uses `gemini-2.5-flash`
- `LLM_PROVIDER=anthropic` — uses `claude-sonnet-4-6`

---

## Key rules
- Each generated scene is fully self-contained: one file, one Scene class
- System prompt quality is the most important factor in output quality
- All prompts live as `.md` files in `prompts/` dirs — never inline Python strings
- Log every generation attempt (code + error) for future prompt improvement
- `codeguard.py` runs deterministic fixes before every LLM call — extend it for new known-bad patterns
- Retry order: codeguard auto-fix → LLM fix → repeat up to 3× → fallback

---

## What's not done yet (Phase 3+)
- `renderer/tts.py` — TTS/narration is a stub, not implemented
- Audio-video sync
- Multi-input (PDFs, images, URLs)
