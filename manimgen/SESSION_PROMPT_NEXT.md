# Next Session Prompt — Issue 1 + Issue 3

## Before anything else

Read `manimgen/CLAUDE.md` top to bottom. It is the authoritative session guide.
Read `MASTER GUIDELINES.md` at the project root — it governs all engineering decisions.
Then read the two failing scene files to understand what the LLM actually generates:
- `manimgen/output/logs/Section03Scene_attempt1.py`
- `manimgen/output/logs/Section04Scene_attempt1.py`

Do NOT write any code before reading those files.

---

## Context

This is the manimgen pipeline: topic string → 3Blue1Brown-style animated video via ManimGL.
Repo: `https://github.com/Varkot-dev/videomaking.git`, branch `main`.
Working directory: `manimgen/` (the subrepo — all source, tests, examples live here).

The last session (2026-04-08) fixed 4 codeguard patterns and added 5 example scenes.
A smoke test ("bubble sort") confirmed 3/5 sections now render. 2 still fall back.

**337 tests pass.** Run them first before touching anything:
```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -v
```

---

## Issue 1 — VGroup swap pattern (40% fallback rate)

### What's failing
Sections 3 and 4 of the bubble sort pipeline fall back on every run.
Both scenes try to swap VGroup elements using Python tuple assignment:
```python
boxes[i], boxes[i+1] = boxes[i+1], boxes[i]       # Section 3, line 77
labels[j], labels[j + 1] = labels[j + 1], labels[j]  # Section 4, line 131
```
VGroup has `__getitem__` but NO `__setitem__`. This crashes with:
```
TypeError: 'VGroup' object does not support item assignment
```
codeguard detects it (banned pattern) but cannot auto-fix it — it's a structural rewrite.
The retry LLM (1-call budget) also fails because 100+ line scenes need full restructuring.

### Why this happens
When a topic involves swapping elements (sort algorithms, card games, permutations), the
Director generates index-tracking swap logic using VGroup subscript assignment — the natural
Python idiom — without knowing VGroup doesn't support it.

### Fix: Two things together (Option A + B from CLAUDE.md)

**A. Add `examples/array_swap_scene.py`**

Write a verified ManimGL scene that shows the correct swap pattern:
```python
# techniques: array_swap
```
The key pattern to demonstrate:
- Build boxes as a VGroup for layout, BUT ALSO keep a parallel Python list `box_list = list(boxes)`
- All index tracking (swap references) is done on `box_list`, never on the VGroup
- Swap animation: `boxes[i].animate.move_to(pos_j)` and `boxes[j].animate.move_to(pos_i)` simultaneously
- After the animation, swap the Python list references: `box_list[i], box_list[j] = box_list[j], box_list[i]`
- Never re-assign into the VGroup. Use `box_list[i]` for future position lookups.

The scene should animate a full pass of bubble sort (array of 5-6 elements, 2-3 swaps visible).
It should be a complete, renderable ManimGL scene — verify it actually runs with manimgl before committing.

`scene_generator._index_examples()` will auto-pick it up when any cue visual contains "swap", "sort", or "exchange" — no code changes needed.

**B. Add a constraint to `planner/prompts/planner_system.md`**

Add to the visual description rules: when describing swap-based algorithm steps, always describe
the visual as physical movement — "box A slides right to box B's position while box B slides left"
— never as "swap array[i] and array[i+1]". This prevents the Director from thinking it needs
to do programmatic index management at all.

### Test
After both changes, clear old scenes and re-run:
```bash
rm -f manimgen/output/scenes/section_0*.py manimgen/output/muxed/section_0*.mp4 manimgen/output/videos/understanding_bubble_sort.mp4
MANIMGEN_MAX_RETRY_LLM_CALLS=1 python3 -m manimgen.cli "bubble sort" --resume 2>&1
```
Target: 5/5 sections render without fallback.

---

## Issue 3 — A/V timing mismatch (freeze-frame padding)

### What's failing
The muxer logged these warnings during the 2026-04-08 smoke test:
```
[muxer] Duration mismatch: video=3.167s audio=5.093s diff=1.926s
[muxer] Duration mismatch: video=4.933s audio=6.036s diff=1.103s
[muxer] Duration mismatch: video=5.467s audio=3.498s diff=1.969s
[muxer] Duration mismatch: video=3.167s audio=5.914s diff=2.748s
```
Video renders shorter than cue audio. Muxer freeze-frame pads so it doesn't crash, but
the last 1–3 seconds of many cues are frozen stills. This is visible in the output video.

### Root cause
The Director is told: "per cue, sum(run_time values) + self.wait() = cue duration exactly."
But when animations are inside loops (e.g. sweeping through N array elements), the total
run_time depends on loop count — which the LLM doesn't always compute correctly.
It writes `self.wait(X)` based on a fixed estimate, not the actual accumulated time.

### Fix: Three-part

**Part 1 — codeguard: clamp negative wait()**
Add to `apply_known_fixes()`: detect `self.wait(` followed by a negative literal or zero,
clamp to `self.wait(0.01)`. A negative wait crashes ManimGL.
```python
# detect: self.wait(-0.3) or self.wait(0.0) or self.wait(0)
```
Add a test to `tests/test_codeguard.py`.

**Part 2 — Director prompt: loop timing rule**
Add to `generator/prompts/director_system.md` under "Cue timing":
> If any animations are inside a loop, compute the total loop run_time in a Python variable
> BEFORE the wait call, then use it: `self.wait(cue_duration - total_anim_time)`.
> Never hardcode the wait when animation count depends on data.

Example to add to the prompt:
```python
# WRONG — hardcoded wait doesn't account for loop iterations
for i in range(n - 1):
    self.play(scan_rect.become(...), run_time=0.2)
self.wait(4.0 - 0.2)   # ← wrong: only subtracts one iteration

# RIGHT — accumulate then subtract
anim_time = 0.0
for i in range(n - 1):
    self.play(scan_rect.become(...), run_time=0.2)
    anim_time += 0.2
self.wait(max(0.01, 4.0 - anim_time))
```

**Part 3 — muxer: bump warning threshold**
`renderer/muxer.py` currently warns at diff > 0.5s. Keep that, but also add a more prominent
WARNING log line at diff > 1.5s so it stands out in pipeline output for large mismatches.

### Test
- Unit test for negative wait clamp: `tests/test_codeguard.py`
- Smoke test: re-run pipeline, confirm muxer warning count drops
- Check mismatch diffs are all < 1.0s after the prompt fix

---

## Workflow

1. New branch from main: `git checkout main && git checkout -b feature/swap-timing-fixes`
2. Fix Issue 1 first (higher impact — eliminates fallback entirely for 2 sections)
3. Fix Issue 3 second
4. Run full test suite — 337 must pass before committing
5. Smoke test: `MANIMGEN_MAX_RETRY_LLM_CALLS=1 python3 -m manimgen.cli "bubble sort" --resume`
6. Commit, push, merge to main
7. Update `CLAUDE.md` Known Issues section to mark these fixed

## What NOT to do
- Do not start fixing before reading the failing scene files
- Do not add docstrings or comments to code you didn't change
- Do not add speculative abstractions — fix exactly what's broken
- Do not commit unless tests pass
