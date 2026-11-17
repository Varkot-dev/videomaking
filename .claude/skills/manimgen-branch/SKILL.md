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

## Branch rule

`main` is the active branch and CI gates every pull request into it. Branch from an up-to-date `main` unless the user names a different parent:

```bash
git checkout main && git pull
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
- `font_size=` on `Tex()` is valid — do not convert it to `.scale()` (double-scales)
- Axes use `width=` and `height=` — never `x_length=` or `y_length=`

## Adding a new example scene

1. Write the scene in `manimgen/manimgen/examples/<technique>_scene.py`
2. Add `techniques: <name>, <name>` as the first line inside the class docstring (each name must exist in `manimgen/techniques.py`; a test enforces it)
3. That's it — `scene_generator._index_examples()` picks it up automatically at runtime

No code changes needed in `scene_generator.py` to register new examples.

## Test cycle

```bash
cd manimgen   # from the repository root
python3 -m pytest -q
ruff check manimgen/ && ruff format --check manimgen/
```

Run the bare pytest command. Never add `--ignore` flags: every test, including `test_scene_generator` and `test_planner`, is fully mocked and costs nothing.

All tests must pass before declaring done. Fix failures before moving on.

## Finish checklist

After all tests pass, tell the user:
- What new files were created and their purpose
- What existing files were changed and why
- Whether `CLAUDE.md` or `MASTER GUIDELINES.md` needs updating
- What you recommend working on next

Ask "Ready to move on?" before switching branches.

## What NOT to do

- Don't commit unless the user asks
- Don't run `manimgl` directly — tests mock it
- Don't call LLM APIs during tests (mock them)
- Don't hardcode mappings that belong in data (files, config, docstrings)
- Don't add docstrings or comments to code you didn't write
- Don't create utilities that aren't immediately used by the pipeline
