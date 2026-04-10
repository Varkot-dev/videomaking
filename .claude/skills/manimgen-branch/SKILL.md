---
name: manimgen-branch
description: Use this skill whenever starting work on a new manimgen pipeline feature, audio/video fix, or any new branch in the 3Blue1Brown project. Triggers on phrases like "let's work on X", "start the next branch", "move on", "next feature", "create a branch for", or any request to begin a new isolated piece of manimgen work. Handles the full branch setup + implementation + test cycle for this project.
---

# ManimGen Branch Workflow

This skill governs how new feature branches are created and implemented in the manimgen pipeline. Every piece of work follows this exact pattern — don't deviate.

## Before touching anything

1. Read `manimgen/CLAUDE.md` — it is the session guide and is kept up to date.
2. Read `MASTER GUIDELINES.md` at the project root — it governs all engineering decisions.
3. Read the relevant source files. Never write code based on assumptions.

Key files by area:
- Audio/TTS: `manimgen/renderer/tts.py`, `manimgen/renderer/muxer.py`
- Planner/schema: `manimgen/planner/lesson_planner.py`, `manimgen/planner/cue_parser.py`
- Scene generation: `manimgen/generator/scene_generator.py`
- Pipeline: `manimgen/cli.py`
- Segmentation: `manimgen/planner/segmenter.py`

## Branch chain rule

All manimgen branches stem from the previous feature branch, not from `main`. Check the current frontier:

```bash
git branch && git log --oneline -5
```

Branch from the most recent feature branch:

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown
git checkout <parent-branch>
git checkout -b feature/<short-kebab-description>
```

## Implementation rules (Master Guidelines)

- **No hardcoded data in code** — if a mapping or list is needed, put it in a file or config and read it. Example: technique→example mapping lives as `techniques:` tags in example docstrings, read by `_index_examples()`.
- **No duplicate sources of truth** — prompts are in `.md` files, not Python strings. Examples are in `examples/`, not inline scaffolds.
- **No speculative abstractions** — build exactly what's needed. Abstract only at the third repetition.
- **Seams over coupling** — new modules should be pure functions where possible. Wire into `cli.py` last.
- **Data-first** — new LLM-facing schemas go in the prompt `.md` files. New config goes in `config.yaml`.

## ManimGL rules (never mix these up)

- Import: `from manimlib import *` — never `from manim import *`
- `ShowCreation` not `Create`, `Tex` not `MathTex`, `self.frame` not `self.camera.frame`
- `font_size=` on `Tex()` crashes — codeguard converts it to `.scale()`
- Axes use `width=` and `height=` — never `x_length=` or `y_length=`

## Adding a new example scene

1. Write the scene in `examples/<technique>_scene.py`
2. Add `techniques: <name>, <name>` as the first line inside the class docstring
3. That's it — `scene_generator._index_examples()` picks it up automatically at runtime

No code changes needed in `scene_generator.py` to register new examples.

## Test cycle

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py -v
```

`test_scene_generator` and `test_planner` make real LLM calls — always skip in local runs.

All tests must pass before declaring done. Fix failures before moving on.

## Finish checklist

After all tests pass, tell the user:
- What new files were created and their purpose
- What existing files were changed and why
- Whether `CLAUDE.md` or `MASTER GUIDELINES.md` needs updating
- What the next branch in the chain is

Ask "Ready to move on?" before switching branches.

## What NOT to do

- Don't commit unless the user asks
- Don't run `manimgl` directly — tests mock it
- Don't call LLM APIs during tests (mock them)
- Don't hardcode mappings that belong in data (files, config, docstrings)
- Don't add docstrings or comments to code you didn't write
- Don't create utilities that aren't immediately used by the pipeline
