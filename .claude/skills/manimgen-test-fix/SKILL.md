---
name: manimgen-test-fix
description: Use when tests fail in the manimgen project, when there's a test failure to diagnose, or when the user says something like "tests are failing", "fix the tests", "something broke". Diagnoses the failure, determines whether it's a code bug or a wrong test assertion, and fixes the right one. Never fixes tests just to make them pass — fixes the actual problem.
---

# ManimGen Test Fix Workflow

When tests fail, diagnose first. Never blindly change a test assertion without understanding why.

## Step 1: Run and read the failure

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py -v 2>&1
```

Read the full traceback — the `AssertionError` line and `E` lines tell you expected vs actual.

## Step 2: Classify

**A — Code bug**: implementation is wrong; test correctly describes expected behaviour.
→ Fix the implementation.

**B — Wrong test assertion**: test expectation is incorrect; code behaviour is right.
→ Fix the test and add a comment explaining why the original was wrong.

Never change a test assertion just to make it green.

## Step 3: Isolate, then verify full suite

```bash
python3 -m pytest tests/test_<file>.py::TestClass::test_name -v
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py -v
```

## Common failure patterns

**Prompt file tests** (`test_director_prompt.py`, `test_planner_prompt.py`): check for exact strings — if you changed a prompt file, check what the test asserts and update the prompt (or the test if the assertion was wrong). The `## Cinematic Technique Reference` section is a marker — everything after it is scanned for banned ManimCommunity APIs, so don't put `x_length=` or `y_length=` after that heading.

**Example docstring tests** (`test_examples.py`): each example must have a `techniques:` tag as the first line of the class docstring. If you added an example without the tag, add it.

**Float precision**: use `abs(a - b) < 1e-6`, not `==`, for timestamp comparisons.

**Audio duration math**: segment durations are `audio_duration - first_word.start`, not `audio_duration`. Pre-speech silence is excluded by design.

**Word count off-by-one**: `"Hello world.".split()` = `["Hello", "world."]` — punctuation attaches to words.

**Async TTS mocking**: patch at `manimgen.renderer.tts.edge_tts.Communicate`, not `edge_tts.Communicate`.

**codeguard regex**: if a new auto-fix pattern accidentally matches valid code, narrow the regex.

## After all tests pass

Report: `N passed`, which files changed, and whether the root cause was a real design issue worth flagging.
