# Why the LLM Calls Keep Failing — Root Cause Investigation

**Date:** 2026-05-24
**Branch:** `investigation/llm-failure-root-cause`
**Method:** Read every saved render log + failed scene file from the last real run (`output/logs/`, `output/scenes/`), cross-checked against the actual pinned ManimGL v1.7.2 source and the `codeguard.py` rule set. Nothing here is a guess — every claim has a traceback or a `file:line` behind it.

---

## Part 1 — The one-sentence version (the "stupid-level" answer)

**Our AI is writing code in the wrong dialect of the same language, and our safety net only knows how to catch a few of the wrong words.**

That's it. Read on and it gets more precise, but if you remember one thing: **the LLM is generating "ManimCommunity" code, but we run "ManimGL". They look almost identical, so the AI confidently writes code that simply doesn't exist in our engine, and it crashes.**

---

## Part 2 — The slightly-less-stupid version (the analogy)

Imagine you hired a brilliant writer who speaks **British English** and told them to write for an **American** audience — but you never clearly told them which one you wanted, and you only gave them a small list of "don't say *lift*, say *elevator*."

They write beautifully. But they keep saying *lorry*, *boot*, *jumper*, *petrol* — words that are perfectly real in British English and totally wrong for you. Your little correction list catches *lift→elevator* but misses *lorry*, *boot*, *jumper*. So a third of their pages come back unusable, and you fall back to a plain typed memo instead.

Now map that back:

| Analogy | Reality |
|---|---|
| British vs American English | **ManimCommunity** vs **ManimGL** — two forks of the `manim` animation library that share most names but differ in the details |
| The brilliant writer | The LLM (Gemini) generating Python animation code |
| Your small "don't say X" list | `codeguard.py` — a hand-written list of ~90 known bad patterns to auto-fix |
| Words it kept getting wrong | `fill_opacity=`, `checkerboard_colors=`, `z_length=`, `begin_ambient_camera_rotation(...)` — all real in ManimCommunity, all nonexistent in ManimGL |
| Falling back to a plain memo | The pipeline rendering a plain bullet-point **"FallbackScene"** when the real animation crashes |

On the last run, **3 of 6 sections fell back or were shaky.** Those `*FallbackScene.mp4` files in your videos folder are the visible scar of every failed generation.

---

## Part 3 — The evidence (what actually crashed, verbatim)

These are the real tracebacks pulled from `output/logs/`. Every section that fell back, failed for the **same family of reason** — not random, not flaky, not "the API was down."

### Section 01 — failed all 3 attempts, then fell back
```
attempt 1:  NameError: name 'MUTED' is not defined
attempt 2:  TypeError: Mobject.__init__() got an unexpected keyword argument 'fill_opacity'
attempt 3:  TypeError: Mobject.__init__() got an unexpected keyword argument 'checkerboard_colors'
```

### Section 02 — failed all 3 attempts, then fell back
```
attempt 1:  TypeError: Mobject.__init__() got an unexpected keyword argument 'checkerboard_colors'
attempt 2:  SyntaxError: closing parenthesis ']' does not match opening parenthesis '('
attempt 3:  TypeError: Mobject.__init__() got an unexpected keyword argument 'fill_color'
```

### Section 03 — failed, produced both a real scene and a fallback
```
attempt 1:  AttributeError: 'Section03Scene' object has no attribute 'begin_ambient_camera_rotation'
attempt 2:  TypeError: CoordinateSystem.__init__() got an unexpected keyword argument 'z_length'
attempt 3:  TypeError: Mobject.__init__() got an unexpected keyword argument 'checkerboard_colors'
```

### Even the "successes" (5, 6) crashed on attempt 1 first
```
Section 05 attempt 1:  TypeError: Mobject.__init__() got an unexpected keyword argument 'x_axis_config'
Section 06 attempt 1:  NameError: name 'ease_out_sine' is not defined
```

**Two patterns, over and over:**
1. **Wrong-fork API** — `fill_opacity`, `checkerboard_colors`, `fill_color`, `z_length`, `x_axis_config`, `begin_ambient_camera_rotation`. (ManimCommunity words.)
2. **Undefined name** — `MUTED`, `STRUCT`, `ease_out_sine`. (The AI *used* a color-role shortcut or a helper it never *defined*.)

And one bonus: attempt 2 of Section 02 was **literally broken Python** (`resolution=20, BLUE_E],` — a mangled line). That's the retry loop's own auto-fixer corrupting the file while trying to patch it.

---

## Part 4 — The technical root cause (three layers, in order of importance)

### Layer 1 — The LLM generates ManimCommunity API against a ManimGL engine *(the dominant cause)*

This is **~80% of the failures.** The two libraries diverge in exactly the places the LLM trusts its training data most.

The killer detail is in ManimGL's source. `Mobject.__init__` (the base class everything inherits from) has a **fixed seven-argument signature with no `**kwargs` catch-all**:

```python
# manimlib/mobject/mobject.py:79-90  (ManimGL v1.7.2)
def __init__(
    self,
    color: ManimColor = WHITE,
    opacity: float = 1.0,
    shading: Tuple[float, float, float] = (0.0, 0.0, 0.0),
    texture_paths: dict[str, str] | None = None,
    is_fixed_in_frame: bool = False,
    depth_test: bool = False,
    z_index: int = 0,
):   # <-- no **kwargs. Anything else is a hard TypeError.
```

`ParametricSurface` forwards all its keyword arguments straight up into that fixed signature. So the moment the LLM writes `fill_opacity=` (a real ManimCommunity kwarg), it hits this wall and dies with the exact error we see: `Mobject.__init__() got an unexpected keyword argument 'fill_opacity'`.

The full translation table the investigation confirmed against source:

| LLM wrote (ManimCommunity) | ManimGL truth | Correct ManimGL idiom |
|---|---|---|
| `fill_opacity=0.8` | rejected | `opacity=0.8` |
| `fill_color=BLUE` | rejected | `color=BLUE` |
| `stroke_color=...` on a surface | rejected | `color=...` (surfaces have no stroke) |
| `checkerboard_colors=[...]` | rejected | build manually / `set_color_by_rgba_func` |
| `z_length=4` on axes | rejected | `depth=4` (and `width`/`height`, not `x_length`/`y_length`) |
| `self.begin_ambient_camera_rotation()` | doesn't exist | `self.frame.add_ambient_rotation(angular_speed=...)` |
| `self.set_camera_orientation(...)` | doesn't exist | `self.frame.reorient(theta, phi)` |

The LLM isn't "dumb." ManimCommunity is the far more popular fork on the public internet, so its API dominates the model's training data. We are asking the model to write in the rarer dialect, and **nothing in the pipeline forcefully grounds it in the real, installed API.**

### Layer 2 — Undefined names: the prompt *shows* a header but never *requires* it

`NameError: name 'MUTED' is not defined` is a self-inflicted wound. The Director prompt (`generator/prompts/director_system.md`) defines a "color role" system:

```python
PRIMARY = TEAL_A   # focal object
STRUCT  = GREY_B   # axes, scaffolding
MUTED   = GREY_A   # supporting text
... etc
```

…and then *uses* `color=MUTED` throughout its examples. But that header block appears **inside a fenced code example**, framed as "here's the palette," not as "**you must emit these 8 assignment lines at the top of every file.**" So under generation pressure the LLM writes `color=MUTED` as if `MUTED` were a built-in constant — and omits the definitions, or defines 6 of the 8. Result: `NameError` at render. Same story for `ease_out_sine` (an easing function the model assumed exists).

### Layer 3 — codeguard is an unbounded denylist that can't keep up *(the architectural cause)*

`codeguard.py` is our safety net: ~90 hand-written rules that auto-fix known-bad patterns *before* render. The investigation counted them and checked each failure against them:

| Failed pattern | Did codeguard catch it? |
|---|---|
| `fill_opacity` / `checkerboard_colors` / `fill_color` on surface | Only **reactively** (after a crash) via a blind "strip the kwarg" fallback |
| `z_length` | No proactive rule (it converts `x_length`/`y_length` but forgot `z`) |
| `x_axis_config` | **No rule at all** |
| `begin_ambient_camera_rotation` | **No rule at all** (no `AttributeError` handler exists) |
| `NameError: MUTED / STRUCT` | **No rule** — the reactive name-fix dict has no entry for the role constants |
| mangled-bracket SyntaxError | Detected (blocks render) but its own fixer can't repair it |

**Of the 8 distinct error classes, codeguard proactively fixes 0.** It reactively papers over ~4 only *after* wasting a render attempt, and has zero coverage for the two cleanest, most-deterministic wins.

The deeper problem (filed as issue **#30**): a denylist is the **wrong shape** for this problem. "Which kwargs does ManimGL reject?" is an *open-ended, infinite* set — every Mobject subclass has its own divergent constructor, and the LLM will always invent a new wrong kwarg next week. You cannot enumerate your way to safety. The correct shape is an **allowlist**: ManimGL legality is a *closed, knowable* set (the symbols that actually exist in the installed `manimlib`), so the question should be inverted to **"does this name/kwarg actually exist in our engine?"** — which is bounded and self-maintaining.

---

## Part 5 — Why retries don't save us (and sometimes hurt)

The pipeline retries a failed scene up to 3 times, feeding the error back to the LLM. But:
- The LLM is retrying **within the same wrong dialect** — it fixes `fill_opacity`, then trips on `checkerboard_colors`, then on `fill_color` (exactly Section 01's three attempts). It's whack-a-mole inside ManimCommunity-land.
- The deterministic auto-fixer sometimes **corrupts the file** while patching (Section 02 attempt 2's bracket-mismatch `SyntaxError` was self-inflicted).
- After 3 misses → **fallback to a plain bullet card.** Which is why your videos folder has `Section01FallbackScene.mp4`, `Section02FallbackScene.mp4`, etc.

So the failure rate compounds: each section is one bad kwarg away from burning all 3 retries and degrading to a memo.

---

## Part 6 — The fix, in priority order (the cherry on top)

If we fix the root cause, the entire quality ceiling lifts — every other improvement (timing, layout, audio) only matters on scenes that actually render.

1. **Ground the LLM in the real API (highest leverage).** Two complementary moves:
   - **Allowlist gate (issue #30, mechanism just landed in shadow mode):** introspect the installed `manimlib` with `inspect.signature` and *reject/flag* any kwarg the real constructor won't accept — *before* render. Bounded, self-maintaining, replaces the infinite denylist. The shadow-mode scaffolding is now in `validator/manimlib_symbols.py`; turning on enforcement once we have shadow data is the next step.
   - **Prompt hardening:** make the "this is ManimGL, NOT ManimCommunity" contract impossible to miss, with the specific kwarg translations from Part 4 as a hard table. The CLAUDE.md already has a version of this; the Director prompt needs the surface/axes/camera kwargs added explicitly.

2. **Deterministically inject the color-role header (cheapest high-value win).** If the generated code references `MUTED`/`STRUCT`/`PRIMARY`/etc. but doesn't define them, codeguard can inject the canonical 8-line header automatically. The mapping already lives in the codebase twice. This kills the entire `NameError` class with ~20 lines. Same trick maps `ease_out_sine → smooth`.

3. **Add the missing codeguard rewrites** for the known ManimGL/Community pairs that have no rule yet: `begin_ambient_camera_rotation → self.frame.add_ambient_rotation`, `z_length → depth`, `x_axis_config`/`y_axis_config` merge into `axis_config`. These are deterministic, analogous to the existing `set_camera_orientation` fixer.

4. **(Larger, later) Structured generation.** Constrain the LLM's output so it physically cannot emit a nonexistent symbol — e.g. generate against a typed schema / restricted API surface rather than free-form Python. This is the durable end-state but is a bigger build; #30's allowlist buys most of the safety meanwhile.

---

## Appendix — Provenance

- **Failure tracebacks:** `manimgen/output/logs/Section0{1..6}Scene_attempt{1..3}.log`
- **Failed scene source:** `manimgen/output/scenes/section_01.py`, `section_02.py` (note the mangled `ParametricSurface` call in 01)
- **ManimGL ground truth:** `3b1b/manim` tag `v1.7.2` (the installed version per `manimgl-1.7.2.dist-info/METADATA`) — `mobject.py:79-90`, `surface.py:37-53/220-226`, `coordinate_systems.py:440-451/533-542`, `camera_frame.py:172-214`
- **codeguard rule inventory:** `manimgen/manimgen/validator/codeguard.py` (~90 rules across `_BANNED_PATTERNS`, `apply_known_fixes`, `_KWARG_NORMALIZATION_REGISTRY`, error-aware `_name_fixes`)
- **Related issues:** #30 (denylist→allowlist), and the Director-prompt grounding work.
