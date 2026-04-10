# Session 1 — Pipeline Reliability

## Context
Project: `manimgen` — automated pipeline: topic → 3Blue1Brown-style video.
Repo: `https://github.com/Varkot-dev/videomaking.git`
Working dir: `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/`
Read `manimgen/CLAUDE.md` before touching any code.

## Setup
```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
git checkout main
git merge feature/swap-timing-fixes   # if not already merged
git pull
git checkout -b feature/pipeline-reliability
```

## Tasks

### 1. Codeguard: loop timing detector
**File:** `manimgen/validator/codeguard.py`

Add a static pass `_check_loop_timing_smells(code: str) -> list[str]` that detects:
- A `self.wait(` call that appears **after** a `for` or `while` block with no accumulator variable (`anim_time` or similar) in that block.

Emit a warning string like:
`"Loop timing: self.wait() after loop at line N — accumulate run_times in anim_time, use self.wait(max(0.01, cue_dur - anim_time))"`

Call it from `precheck_and_autofix()` and append warnings to the existing warnings list.

Write `tests/test_codeguard_loop_timing.py` — at minimum: detects missing accumulator, passes clean code.

### 2. Layout checker: structured feedback
**File:** `manimgen/validator/prompts/layout_checker_system.md`

Rewrite the prompt so the LLM returns either:
- The literal string `OK` if no issues
- One or more lines in this exact format:
  ```
  ISSUE | <what's wrong> | CAUSE | <why it happens in ManimGL> | FIX | <exact code pattern to use instead>
  ```

**File:** `manimgen/validator/retry.py`

After `check_layout()` returns `{"ok": False, "issues": "..."}`, parse the `ISSUE|CAUSE|FIX` lines and prepend them to the LLM fix prompt as:
```
Visual defects detected:
- ISSUE: ... CAUSE: ... FIX: ...
```

Write `tests/test_layout_checker.py` — mock the LLM call, verify `check_layout()` returns correct structure for OK and ISSUE responses.

### 3. Muxer: visibility
**File:** `manimgen/renderer/muxer.py`

- Change `_WARN_THRESHOLD_SECONDS = 0.5` → `1.0`
- In `mux_audio_video()`, return a dict `{"output": path, "diff": diff}` instead of just `path`, OR add a module-level list `_mismatch_log` that the CLI can print at the end. Choose whichever is cleaner given the current call sites.

## Verification
```bash
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -v
```
All tests must pass before committing.

## Do NOT touch
`examples/`, `planner_system.md`, `director_system.md`, `scene_generator.py`
