# ManimGen — Claude Code Guide

## What this project is
Automated pipeline: topic string **or PDF** → 3Blue1Brown-style animated explainer video with narration.
Full pipeline (generation → retry → fallback → TTS → mux → assemble → edit) runs end-to-end.

**Stack:** Python 3.9+, ManimGL (3b1b), Gemini API (dev) / Claude API (prod), FFmpeg, LaTeX, edge-tts, Flask (editor)

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

Mixing the two APIs is the #1 source of bugs. `codeguard.py` auto-fixes many of these.

---

## Project structure

```
manimgen/
├── manimgen/               # source package
│   ├── cli.py              # entry point: manimgen <topic> | manimgen --pdf <file>
│   ├── llm.py              # shared LLM client (Gemini/Anthropic toggle)
│   ├── input/
│   │   ├── parser.py       # normalize topic string
│   │   └── pdf_parser.py   # extract + clean text from PDF
│   ├── planner/
│   │   ├── lesson_planner.py   # LLM → structured lesson plan JSON
│   │   └── prompts/            # planner_system.md, planner_pdf_system.md
│   ├── generator/
│   │   ├── scene_generator.py  # LLM → ManimGL scene code (with duration hints)
│   │   └── prompts/            # generator_system.md, rules_core.md
│   ├── validator/
│   │   ├── codeguard.py        # static checks + auto-fixes (token-free)
│   │   ├── runner.py           # subprocess manimgl, logs attempt
│   │   ├── retry.py            # retry loop (codeguard → error-aware fix → LLM fix)
│   │   ├── fallback.py         # title-text fallback scene
│   │   ├── layout_checker.py   # LLM vision check on rendered frames
│   │   └── env.py              # render environment vars (LaTeX PATH)
│   ├── renderer/
│   │   ├── assembler.py        # FFmpeg concat of section videos
│   │   ├── tts.py              # edge-tts narration generation
│   │   └── muxer.py            # audio-video mux (speed/loop/pad strategies)
│   └── editor/
│       ├── server.py           # Flask UI for clip review, reorder, trim, export
│       └── templates/editor.html
├── examples/               # 5 hand-written verified ManimGL scenes (few-shot seeds)
├── tests/                  # pytest tests (codeguard, muxer, pdf_parser, planner, scene_gen)
├── config.yaml             # LLM provider, output dirs, render quality, TTS config
├── requirements.txt
└── setup.py                # console_scripts: manimgen, manimgen-edit
```

---

## Pipeline flow

```
topic string or PDF
  → parse_input() / pdf_parser.extract_text()
  → plan_lesson() / plan_lesson_from_pdf()
      LLM → {title, sections: [{id, title, visual_description, narration, ...}]}
      Hard cap: 8 sections (topic) / 10 sections (PDF)
  → for each section:
      generate_scenes()     LLM → ManimGL .py file (with target duration hint)
      run_scene()           manimgl subprocess → video, or fail
      retry_scene()         codeguard auto-fix → error-aware fix → LLM fix (budget-capped)
      fallback_scene()      title-text fallback if all retries fail
      _add_narration()      TTS → .mp3, then mux onto video (skipped for fallbacks)
  → assemble_video()        FFmpeg concat → final .mp4
  → manimgen-edit           browser UI for clip reorder/trim/export
```

---

## Running the pipeline

```bash
# from manimgen/ project root
export GEMINI_API_KEY=...      # or ANTHROPIC_API_KEY + LLM_PROVIDER=anthropic
manimgen "binary search"       # topic mode
manimgen --pdf notes.pdf       # PDF mode
manimgen-edit                  # launch clip editor (defaults to muxed/ dir)
```

Output: `manimgen/output/videos/<title>.mp4`
Muxed clips: `manimgen/output/muxed/`
Editor exports: `manimgen/output/videos/exports/`

---

## LLM provider toggle

Resolution order (first wins):
1. `LLM_PROVIDER` env var
2. `llm_provider` in `config.yaml`
3. Default: `"gemini"`

Providers:
- `gemini` — uses `gemini-2.5-flash`
- `anthropic` — uses `claude-sonnet-4-6`

---

## Key rules
- Each generated scene is fully self-contained: one file, one Scene class
- System prompt quality is the most important factor in output quality
- All prompts live as `.md` files in `prompts/` dirs — never inline Python strings
- Log every generation attempt (code + error) for future prompt improvement
- `codeguard.py` runs deterministic fixes before every LLM call — extend it for new known-bad patterns
- Retry order: codeguard auto-fix → error-aware fix (token-free) → LLM fix (budget-capped) → fallback
- Token budget: `MAX_LLM_FIX_CALLS` (default 1) limits paid retry calls; set `MANIMGEN_MAX_RETRY_LLM_CALLS=0` for local-fix-only retries
- TTS is skipped for fallback scenes to avoid severe duration mismatch

---

## PDF parsing philosophy — DO NOT compromise on extraction quality

This project exists to turn learning material into educational videos. If we don't extract the full content of the source material, the whole pipeline is pointless.

**The correct 3-way logic for `pdf_parser.py` + `lesson_planner.py`:**

| PDF type | What to do |
|---|---|
| Text-only | Extract text only. No need to render pages — there are no meaningful visuals. |
| Image-only (scanned/diagram PDF) | Render all pages to PNG. Send images to LLM. |
| Mixed (text + diagrams/figures) | Extract text AND render pages. Send both to LLM. Diagrams add real context. |

**Never skip images just to save API time.** A lecture PDF with equations, graphs, or figures alongside text should always send both — the visual content is part of the learning material.

**How to detect PDF type:**
- Pre-scan a few pages with `pypdf` to check for extractable text
- If text found → extract text from all pages
- Render pages to PNG only when the page has no extractable text (mixed case) or the whole PDF is image-only
- Always pass whatever was extracted (text + images) to the LLM planner

**Known performance issue (not yet fixed):**
Currently `pdf_parser.py` renders every page to PNG unconditionally, even text-heavy PDFs. This makes Gemini slow because it processes 10 large image renders + 24k chars of text. The fix is the 3-way logic above — but do it correctly, don't just drop images.

---

## Config note
`config.yaml` defines `output.*` and `rendering.*` keys, but output paths and render flags are currently hardcoded in the Python modules. The `tts.*` and `llm_provider` keys are the only config values actively read by code. Wiring the remaining config keys is a future cleanup task.
