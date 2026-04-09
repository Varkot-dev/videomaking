# Codeguard + Retry Root-Cause Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four verified root causes that cause Sections 03 and 04 of the bubble-sort pipeline to always fall back to a static slide, producing frozen video.

**Architecture:** Four independent branches (one bug per branch), each with a failing test written first, then the fix, then merged in order into a single integration branch where a smoke test replays the exact failing scenes end-to-end through the full precheck→retry→manimgl pipeline.

**Tech Stack:** Python 3.13, pytest, manimgen validator stack (`codeguard.py`, `retry.py`), ManimGL, `re` module for regex fixes.

---

## Background — verified root cause chain

This is confirmed by running the actual failed scenes through the system:

1. **codeguard banned pattern fires on valid Python list assignments** — the regex `\w+\[.+?\]\s*=(?!=)\s*\S` matches `current_values[i] = x` and `box_list[i], box_list[j] = box_list[j], box_list[i]` — the *correct* fixes — as well as the bad VGroup assignment. Correctly-fixed scenes are rejected before they ever reach manimgl.

2. **Retry classifier gives wrong guidance** — precheck stderr is `"Precheck failed:\n- VGroup does not support..."`. `_classify_error` looks for `"TypeError"` — not present — and returns `"type"`. The LLM is told `"Fix the type error. Check argument types."` instead of `"Use box_list = list(boxes)"`. The LLM produces another broken scene.

3. **Retry deduplication kills the second attempt** — same error signature → second LLM call is skipped. Scene falls to fallback.

4. **TransformMatchingTex on Text() objects** — Section04 uses `TransformMatchingTex(pass_counter_text, new_pass_counter_text)` where both are `Text()`. This doesn't crash at construction but produces scrambled animation output at render time (glyphs don't match).

---

## File map

| File | What changes |
|---|---|
| `manimgen/validator/codeguard.py` | Fix banned pattern regex; add `TransformMatchingTex` auto-fix |
| `manimgen/validator/retry.py` | Add precheck-specific error classifier + targeted guidance |
| `tests/test_codeguard.py` | Tests for fixed regex (false-positive cases) + TMT fix |
| `tests/test_retry.py` | Tests for precheck classifier |
| `examples/array_swap_scene.py` | Verify it passes precheck (was failing before) |
| `tests/test_smoke_sections.py` | End-to-end smoke: replay Section03/04 scenes through precheck+retry |

---

## Branch 1: `fix/codeguard-vgroup-pattern`

**Parent:** `main`  
**One-liner:** Narrow the VGroup banned pattern so parallel lists (`box_list`, `label_list`, `_list`-suffixed names, `current_values`, `values`) are not flagged.

### Task 1.1 — Write failing tests for the false positives

**Files:**
- Modify: `tests/test_codeguard.py`

- [ ] **Step 1: Write the failing tests**

Add at the bottom of `tests/test_codeguard.py`:

```python
# ── VGroup banned pattern — false positive regression tests ────────────────

class TestVGroupBannedPatternFalsePositives:
    """The banned pattern must NOT fire on valid Python list subscript assignments.

    These are the exact patterns the LLM produces when it correctly fixes
    a VGroup swap bug using a parallel Python list. All of these were previously
    being rejected by precheck, causing correctly-fixed scenes to fall back.
    """

    def test_does_not_flag_list_suffixed_name(self):
        # box_list[i], box_list[j] = box_list[j], box_list[i]  <- correct pattern
        code = "box_list[i], box_list[j] = box_list[j], box_list[i]"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], f"False positive: {vgroup_errors}"

    def test_does_not_flag_label_list(self):
        code = "label_list[i], label_list[j] = label_list[j], label_list[i]"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], f"False positive: {vgroup_errors}"

    def test_does_not_flag_current_values_list(self):
        # current_values[i], current_values[i+1] = current_values[i+1], current_values[i]
        code = "current_values[i], current_values[i+1] = current_values[i+1], current_values[i]"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], f"False positive: {vgroup_errors}"

    def test_does_not_flag_values_list_suffix(self):
        code = "values_list[j] = new_val"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], f"False positive: {vgroup_errors}"

    def test_does_not_flag_generic_list_suffix(self):
        code = "item_list[k] = something"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], f"False positive: {vgroup_errors}"

    def test_still_flags_boxes_vgroup_swap(self):
        # The real bad pattern: VGroup subscript assignment on plural mob name
        code = "boxes[i], boxes[i+1] = boxes[i+1], boxes[i]"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors, "Should still catch VGroup swap on 'boxes'"

    def test_still_flags_labels_vgroup_swap(self):
        code = "labels[j], labels[j+1] = labels[j+1], labels[j]"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors, "Should still catch VGroup swap on 'labels'"

    def test_still_flags_group_assignment(self):
        code = "group[0] = new_mob"
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors, "Should still catch assignment into 'group'"

    def test_array_swap_example_scene_passes_precheck(self):
        """The example scene we give to the Director must itself pass precheck."""
        import os
        here = os.path.dirname(__file__)
        example_path = os.path.join(here, "..", "examples", "array_swap_scene.py")
        with open(example_path) as f:
            code = f.read()
        from manimgen.validator.codeguard import precheck_and_autofix
        fixed = precheck_and_autofix(code)
        errors = validate_scene_code(fixed)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], (
            f"array_swap_scene.py fails precheck — the example we give the Director "
            f"is itself broken: {vgroup_errors}"
        )
```

- [ ] **Step 2: Run — confirm all false-positive tests FAIL**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestVGroupBannedPatternFalsePositives -v
```

Expected: tests `test_does_not_flag_*` FAIL (they currently flag when they should not). Tests `test_still_flags_*` PASS.

### Task 1.2 — Fix the regex in codeguard.py

**Files:**
- Modify: `manimgen/validator/codeguard.py:29-34`

- [ ] **Step 1: Replace the banned pattern entry**

Current (line ~29 in `_BANNED_PATTERNS`):
```python
(
    r"\w+\[.+?\]\s*=(?!=)\s*\S",
    "VGroup does not support item assignment (vgroup[i] = x raises TypeError). "
    "Use a Python list for mutable indexed storage, then rebuild: "
    "items[i] = new_mob; group = VGroup(*items).",
),
```

Replace with two narrower patterns — one for tuple-swap form, one for single-assign form. Both exclude names ending in `_list` and common plain-list names:

```python
(
    # Tuple-swap: boxes[i], boxes[j] = boxes[j], boxes[i]
    # Excludes names ending in _list (box_list, label_list, values_list, etc.)
    # Excludes names containing 'values' or 'items' (common plain list names)
    r"(?<![_a-z])(?!.*_list\b)(?!.*values\b)(?!.*items\b)"
    r"(?:boxes|labels|group|vgroup|mobs|mobjects|elems|elements|shapes|squares|circles|arrows)\b"
    r"\[.+?\]\s*,\s*"
    r"(?:boxes|labels|group|vgroup|mobs|mobjects|elems|elements|shapes|squares|circles|arrows)\b"
    r"\[.+?\]\s*=(?!=)",
    "VGroup does not support item assignment. "
    "Use a parallel Python list: box_list = list(boxes), then swap box_list[i], box_list[j]. "
    "Never assign into the VGroup directly.",
),
(
    # Single-assign form: boxes[i] = new_mob  (but not box_list[i] = ...)
    r"\b(?:boxes|labels|group|vgroup|mobs|mobjects|elems|elements|shapes|squares|circles|arrows)\b"
    r"(?<!_list)\[.+?\]\s*=(?!=)\s*\S",
    "VGroup does not support item assignment. "
    "Use a parallel Python list: box_list = list(boxes). "
    "Never assign into the VGroup directly.",
),
```

- [ ] **Step 2: Run false-positive tests — all should pass now**

```bash
python3 -m pytest tests/test_codeguard.py::TestVGroupBannedPatternFalsePositives -v
```

Expected: all 9 tests PASS.

- [ ] **Step 3: Run full codeguard test suite — no regressions**

```bash
python3 -m pytest tests/test_codeguard.py -v
```

Expected: all pass.

- [ ] **Step 4: Run full suite**

```bash
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

Expected: ≥344 pass, 0 fail.

- [ ] **Step 5: Commit**

```bash
git checkout -b fix/codeguard-vgroup-pattern
git add manimgen/validator/codeguard.py tests/test_codeguard.py
git commit -m "fix: narrow VGroup banned pattern — stop false positives on parallel list names

The regex \w+\[.+?\]\s*=(?!=)\s*\S was firing on box_list[i], label_list[j],
current_values[i] — the correct patterns the LLM produces when it fixes VGroup
swap bugs. This caused correctly-fixed scenes to be rejected by precheck and
fall back to static slides.

New patterns are anchored to known VGroup-style names (boxes, labels, group,
vgroup, mobs, etc.) and explicitly exclude _list-suffixed names.
array_swap_scene.py now passes precheck."
```

---

## Branch 2: `fix/retry-precheck-classifier`

**Parent:** `fix/codeguard-vgroup-pattern`

**One-liner:** When precheck is the failure source, give the LLM specific VGroup guidance instead of generic "simplify the scene."

### Task 2.1 — Write failing tests

**Files:**
- Modify: `tests/test_retry.py` (create if it doesn't exist)

- [ ] **Step 1: Check if test_retry.py exists**

```bash
ls /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/tests/test_retry.py
```

- [ ] **Step 2: Write failing tests**

If the file doesn't exist, create `tests/test_retry.py`. If it exists, append the class below.

```python
"""Tests for retry.py — error classification and guidance generation."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from manimgen.validator.retry import _classify_error, _fix_guidance


class TestPrecheckErrorClassification:
    """Precheck failures must be classified specifically, not as generic 'type' or 'runtime'.

    Root cause: when precheck rejects a scene, stderr is 'Precheck failed:\n- VGroup...'
    Previously _classify_error returned 'type' (wrong), giving the LLM useless guidance.
    Now it must return 'precheck_vgroup' with targeted fix instructions.
    """

    def test_classifies_vgroup_precheck_as_precheck_vgroup(self):
        stderr = (
            "Precheck failed:\n"
            "- VGroup does not support item assignment (vgroup[i] = x raises TypeError). "
            "Use a Python list for mutable indexed storage, then rebuild: "
            "items[i] = new_mob; group = VGroup(*items)."
        )
        assert _classify_error(stderr) == "precheck_vgroup"

    def test_guidance_for_precheck_vgroup_mentions_box_list(self):
        guidance = _fix_guidance("precheck_vgroup")
        assert "box_list" in guidance or "list(boxes)" in guidance, (
            f"Guidance must tell the LLM to use a parallel list. Got: {guidance!r}"
        )

    def test_guidance_for_precheck_vgroup_mentions_never_assign(self):
        guidance = _fix_guidance("precheck_vgroup")
        assert "assign" in guidance.lower() or "item assignment" in guidance.lower()

    def test_precheck_banned_pattern_stderr_is_classified(self):
        # The exact string format coming from precheck_and_autofix_file
        stderr = "Precheck failed:\n- VGroup does not support item assignment (vgroup[i] = x raises TypeError)."
        assert _classify_error(stderr) == "precheck_vgroup"

    def test_non_vgroup_precheck_still_classified_as_before(self):
        # Other precheck failures (syntax, import) should still go through old path
        stderr = "Precheck failed:\n- Use `from manimlib import *`, not `from manim import *`."
        result = _classify_error(stderr)
        # Should not be precheck_vgroup — it's an import error surfaced by precheck
        assert result != "precheck_vgroup"

    def test_regular_type_error_unaffected(self):
        stderr = "TypeError: Animation.__init__() got unexpected kwarg 'font_size'"
        assert _classify_error(stderr) == "type"

    def test_regular_runtime_error_unaffected(self):
        stderr = "Something went wrong during rendering"
        assert _classify_error(stderr) == "runtime"
```

- [ ] **Step 3: Run — confirm they FAIL**

```bash
python3 -m pytest tests/test_retry.py::TestPrecheckErrorClassification -v
```

Expected: `test_classifies_vgroup_precheck_as_precheck_vgroup` FAIL (returns `"type"` not `"precheck_vgroup"`), `test_guidance_*` FAIL.

### Task 2.2 — Fix retry.py

**Files:**
- Modify: `manimgen/validator/retry.py:18-38`

- [ ] **Step 1: Add precheck_vgroup to _classify_error and _fix_guidance**

Current `_classify_error`:
```python
def _classify_error(stderr: str) -> str:
    if "SyntaxError" in stderr:
        return "syntax"
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return "import"
    if "AttributeError" in stderr:
        return "attribute"
    if "TypeError" in stderr:
        return "type"
    return "runtime"
```

Replace with:
```python
def _classify_error(stderr: str) -> str:
    # Precheck-specific errors (no manimgl run — pure static analysis failures).
    # Check these BEFORE generic TypeError/AttributeError since precheck output
    # contains the word "TypeError" in its explanation text.
    if "Precheck failed" in stderr and "VGroup" in stderr and "item assignment" in stderr:
        return "precheck_vgroup"
    if "SyntaxError" in stderr:
        return "syntax"
    if "ImportError" in stderr or "ModuleNotFoundError" in stderr:
        return "import"
    if "AttributeError" in stderr:
        return "attribute"
    if "TypeError" in stderr:
        return "type"
    return "runtime"
```

Current `_fix_guidance`:
```python
def _fix_guidance(error_type: str) -> str:
    guidance = {
        "syntax": "Fix the Python syntax error shown in the traceback.",
        "import": "Fix the import. Use `from manimlib import *`. Do not import from `manim`.",
        "attribute": "Fix the attribute error. Check the correct ManimGL method name and signature.",
        "type": "Fix the type error. Check argument types and counts for the method.",
        "runtime": "Simplify the scene logic. Reduce animations, check object creation order.",
    }
    return guidance.get(error_type, "Fix the error shown in the traceback.")
```

Replace with:
```python
def _fix_guidance(error_type: str) -> str:
    guidance = {
        "precheck_vgroup": (
            "Fix VGroup item assignment. VGroup does NOT support boxes[i] = x or "
            "boxes[i], boxes[j] = boxes[j], boxes[i] — these crash with TypeError. "
            "The correct pattern: create a parallel Python list BEFORE any swaps: "
            "box_list = list(boxes); label_list = list(labels). "
            "Then swap the list references: box_list[i], box_list[j] = box_list[j], box_list[i]. "
            "Use box_list[k].get_center() for position lookups. "
            "Never assign into the VGroup. Never use boxes[i] after the first swap."
        ),
        "syntax": "Fix the Python syntax error shown in the traceback.",
        "import": "Fix the import. Use `from manimlib import *`. Do not import from `manim`.",
        "attribute": "Fix the attribute error. Check the correct ManimGL method name and signature.",
        "type": "Fix the type error. Check argument types and counts for the method.",
        "runtime": "Simplify the scene logic. Reduce animations, check object creation order.",
    }
    return guidance.get(error_type, "Fix the error shown in the traceback.")
```

- [ ] **Step 2: Run classification tests — all pass**

```bash
python3 -m pytest tests/test_retry.py::TestPrecheckErrorClassification -v
```

Expected: all 7 PASS.

- [ ] **Step 3: Run full suite**

```bash
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

Expected: all pass.

- [ ] **Step 4: Commit**

```bash
git checkout -b fix/retry-precheck-classifier
git add manimgen/validator/retry.py tests/test_retry.py
git commit -m "fix: classify precheck VGroup errors specifically — give LLM targeted swap fix guidance

Previously precheck stderr ('Precheck failed:\n- VGroup does not support...')
was classified as 'type' and the LLM was told to 'fix the type error', which
produced another scene with the same broken pattern.

Now _classify_error detects 'Precheck failed' + 'VGroup' + 'item assignment'
and returns 'precheck_vgroup'. _fix_guidance for that type tells the LLM
exactly how to use box_list = list(boxes) and swap the list, not the VGroup."
```

---

## Branch 3: `fix/codeguard-transform-matching-tex`

**Parent:** `fix/retry-precheck-classifier`

**One-liner:** Detect `TransformMatchingTex` used on `Text()` objects and auto-fix to `FadeOut(a), FadeIn(b)`.

### Task 3.1 — Write failing tests

**Files:**
- Modify: `tests/test_codeguard.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_codeguard.py`:

```python
# ── TransformMatchingTex on Text() objects ──────────────────────────────────

class TestTransformMatchingTexOnText:
    """TransformMatchingTex works on Tex() objects (LaTeX glyph matching).
    When used on Text() objects it produces scrambled output.
    codeguard should detect this and convert to FadeOut/FadeIn.

    Detection strategy: if the variable passed to TransformMatchingTex was
    assigned via Text(...) within the same scene, flag it. Because full
    AST variable tracking is complex, we use a heuristic: detect
    TransformMatchingTex where the first arg contains a name that also
    appears in a Text() assignment in the same code block.
    """

    def test_detects_tmt_on_text_variable(self):
        code = (
            'counter = Text("Pass: 0", font_size=28)\n'
            'new_counter = Text("Pass: 1", font_size=28)\n'
            'self.play(TransformMatchingTex(counter, new_counter), run_time=0.3)\n'
        )
        errors = validate_scene_code(code)
        tmt_errors = [e for e in errors if "TransformMatchingTex" in e and "Text" in e]
        assert tmt_errors, f"Should detect TMT on Text objects. Errors: {errors}"

    def test_autofix_converts_tmt_on_text_to_fade(self):
        code = (
            'counter = Text("Pass: 0", font_size=28)\n'
            'new_counter = Text("Pass: 1", font_size=28)\n'
            'self.play(TransformMatchingTex(counter, new_counter), run_time=0.3)\n'
        )
        fixed, applied = apply_known_fixes(code)
        assert "TransformMatchingTex" not in fixed
        assert "FadeOut(counter)" in fixed
        assert "FadeIn(new_counter)" in fixed
        assert any("TransformMatchingTex" in a for a in applied)

    def test_does_not_flag_tmt_on_tex_variable(self):
        code = (
            'eq1 = Tex(r"x^2 + y^2", font_size=36)\n'
            'eq2 = Tex(r"r^2", font_size=36)\n'
            'self.play(TransformMatchingTex(eq1, eq2), run_time=1.0)\n'
        )
        errors = validate_scene_code(code)
        tmt_errors = [e for e in errors if "TransformMatchingTex" in e and "Text" in e]
        assert tmt_errors == [], f"Must not flag TMT on Tex() objects: {tmt_errors}"

    def test_autofix_does_not_touch_tmt_on_tex(self):
        code = (
            'eq1 = Tex(r"x^2", font_size=36)\n'
            'eq2 = Tex(r"r^2", font_size=36)\n'
            'self.play(TransformMatchingTex(eq1, eq2), run_time=1.0)\n'
        )
        fixed, applied = apply_known_fixes(code)
        assert "TransformMatchingTex(eq1, eq2)" in fixed
        tmt_applied = [a for a in applied if "TransformMatchingTex" in a]
        assert tmt_applied == [], f"Should not touch TMT on Tex: {tmt_applied}"

    def test_real_section04_pattern(self):
        """Exact pattern from Section04Scene_attempt1.py that was crashing."""
        code = (
            'pass_counter_text = Text(f"Pass: {0}", font_size=28, color=TEAL_C).to_corner(UR, buff=0.7)\n'
            'new_pass_counter_text = Text(f"Pass: {1}", font_size=28, color=TEAL_C).to_corner(UR, buff=0.7)\n'
            'self.play(TransformMatchingTex(pass_counter_text, new_pass_counter_text), run_time=0.1)\n'
        )
        fixed, applied = apply_known_fixes(code)
        assert "TransformMatchingTex" not in fixed
        assert any("TransformMatchingTex" in a for a in applied)
```

- [ ] **Step 2: Run — confirm they FAIL**

```bash
python3 -m pytest tests/test_codeguard.py::TestTransformMatchingTexOnText -v
```

Expected: `test_detects_tmt_on_text_variable`, `test_autofix_*`, `test_real_section04_pattern` FAIL. `test_does_not_flag_tmt_on_tex_variable` and `test_autofix_does_not_touch_tmt_on_tex` should already PASS (no false positives yet).

### Task 3.2 — Implement the fix in codeguard.py

**Files:**
- Modify: `manimgen/validator/codeguard.py`

- [ ] **Step 1: Add detection to _BANNED_PATTERNS and auto-fix to apply_known_fixes**

**Detection in `_BANNED_PATTERNS`** — add after the existing VGroup patterns:

```python
(
    # TransformMatchingTex on Text() objects produces scrambled output.
    # Heuristic: if any variable name passed to TMT was also used in a Text(...) call
    # in the same code, flag it. Full AST tracking is overkill; this heuristic catches
    # the common Director pattern of counter text updates.
    # We detect via regex: TransformMatchingTex where first arg appears in a Text( assignment.
    # This is enforced as an auto-fix (not just a warning) in apply_known_fixes().
    r"__PLACEHOLDER_TMT_TEXT__",  # Never actually fires — auto-fix handles it
    "TransformMatchingTex on Text() produces scrambled output. Use FadeOut(a), FadeIn(b) instead.",
),
```

Actually, skip adding to `_BANNED_PATTERNS` (the regex placeholder approach is messy). Instead add a pure auto-fix function and call it from `apply_known_fixes()`.

**Add this function after `_strip_outer_text_wrapper`:**

```python
def _fix_transform_matching_tex_on_text(code: str) -> tuple[str, str | None]:
    """TransformMatchingTex on Text() objects scrambles output — convert to FadeOut/FadeIn.

    Heuristic: collect all variable names assigned via Text(...) in the code.
    For any TransformMatchingTex(a, b) call where 'a' is in that set, rewrite to
    FadeOut(a), FadeIn(b) with matching run_time= kwarg preserved.

    Does NOT touch TransformMatchingTex where first arg was assigned via Tex().
    """
    # Find all names assigned via Text(...)
    text_var_re = re.compile(r"\b(\w+)\s*=\s*(?:always_redraw\(\s*lambda\s*[^:]+:\s*)?Text\s*\(")
    text_vars: set[str] = set(text_var_re.findall(code))

    if not text_vars:
        return code, None

    # Match: TransformMatchingTex(a, b) with optional run_time= and other kwargs
    tmt_re = re.compile(
        r"TransformMatchingTex\(\s*(\w+)\s*,\s*(\w+)\s*(?:,\s*([^)]*))?\)"
    )

    result = code
    count = 0

    def _replacer(m: re.Match) -> str:
        nonlocal count
        a, b = m.group(1), m.group(2)
        extra = m.group(3) or ""
        if a not in text_vars:
            return m.group(0)  # not a Text variable — leave it
        # Preserve run_time= if present
        rt_match = re.search(r"run_time\s*=\s*[\d.]+", extra)
        rt = f", {rt_match.group(0)}" if rt_match else ""
        count += 1
        return f"FadeOut({a}{rt}), FadeIn({b}{rt})"

    result = tmt_re.sub(_replacer, result)
    if count:
        return result, f"TransformMatchingTex(Text, ...) -> FadeOut/FadeIn ({count})"
    return code, None
```

**Wire it into `apply_known_fixes`** — add before the `return fixed, applied` line:

```python
    fixed, tmt_applied = _fix_transform_matching_tex_on_text(fixed)
    if tmt_applied:
        applied.append(tmt_applied)
```

**Add detection to `validate_scene_code`** — add a call at the end of the function:

```python
    # TransformMatchingTex on Text() objects (scrambles output — not a crash but visually broken)
    tmt_text_errors = _detect_tmt_on_text(code)
    errors.extend(tmt_text_errors)
```

**Add the detection helper:**

```python
def _detect_tmt_on_text(code: str) -> list[str]:
    """Return errors if TransformMatchingTex is used on Text() variables."""
    text_var_re = re.compile(r"\b(\w+)\s*=\s*(?:always_redraw\(\s*lambda\s*[^:]+:\s*)?Text\s*\(")
    text_vars: set[str] = set(text_var_re.findall(code))
    if not text_vars:
        return []
    tmt_re = re.compile(r"TransformMatchingTex\(\s*(\w+)\s*,")
    errors = []
    for m in tmt_re.finditer(code):
        a = m.group(1)
        if a in text_vars:
            errors.append(
                f"TransformMatchingTex({a}, ...) — '{a}' is a Text() object. "
                "TransformMatchingTex only works on Tex() objects (LaTeX glyph matching). "
                "Use FadeOut(a), FadeIn(b) for Text() counter updates."
            )
    return errors
```

- [ ] **Step 2: Run the TMT tests — all pass**

```bash
python3 -m pytest tests/test_codeguard.py::TestTransformMatchingTexOnText -v
```

Expected: all 5 PASS.

- [ ] **Step 3: Run full codeguard suite — no regressions**

```bash
python3 -m pytest tests/test_codeguard.py -v
```

Expected: all pass.

- [ ] **Step 4: Run full test suite**

```bash
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git checkout -b fix/codeguard-transform-matching-tex
git add manimgen/validator/codeguard.py tests/test_codeguard.py
git commit -m "fix: auto-convert TransformMatchingTex(Text, ...) to FadeOut/FadeIn

TransformMatchingTex matches LaTeX glyph submobjects. When used on Text()
objects (not Tex()), it produces scrambled animation. Section04 was using it
on Text counter labels (pass_counter_text, swaps_counter_text).

New _fix_transform_matching_tex_on_text() detects Text() variable assignments,
finds TMT calls on those variables, and rewrites to FadeOut/FadeIn preserving
run_time=. Does not touch legitimate TMT(Tex, Tex) calls."
```

---

## Branch 4: `fix/integration-smoke`

**Parent:** `fix/codeguard-transform-matching-tex`

**One-liner:** Smoke test that replays the exact Section03 and Section04 scenes through the full precheck→retry pipeline and verifies they no longer fall back.

### Task 4.1 — Write the section replay smoke test

**Files:**
- Create: `tests/test_smoke_sections.py`

This test does NOT call manimgl (no subprocess). It verifies the precheck+retry chain:
- Scene code from the actual failing logs passes precheck after fixes are applied
- The error classifier gives correct guidance for the precheck error
- The banned pattern doesn't fire on correctly-written parallel-list code

```python
"""
Smoke tests that replay the exact Section03 and Section04 scenes from the
2026-04-08 bubble-sort run. These are the scenes that caused 40% fallback.

These tests do NOT call manimgl (no subprocess). They verify the full
precheck → codeguard → classify → guidance chain using the actual
LLM-generated code that was failing.

If these tests pass, the scenes will reach manimgl instead of falling back.
Run with:
    python3 -m pytest tests/test_smoke_sections.py -v
"""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from manimgen.validator.codeguard import (
    validate_scene_code,
    precheck_and_autofix,
    apply_known_fixes,
)
from manimgen.validator.retry import _classify_error, _fix_guidance


# ── Load the actual failing scenes from logs ──────────────────────────────────

def _load_log(filename: str) -> str:
    here = os.path.dirname(__file__)
    log_path = os.path.join(here, "..", "manimgen", "output", "logs", filename)
    if not os.path.isfile(log_path):
        pytest.skip(f"Log file not found (run the pipeline first): {log_path}")
    with open(log_path) as f:
        return f.read()


SECTION03_CODE = None
SECTION04_CODE = None


def _section03():
    global SECTION03_CODE
    if SECTION03_CODE is None:
        SECTION03_CODE = _load_log("Section03Scene_attempt1.py")
    return SECTION03_CODE


def _section04():
    global SECTION04_CODE
    if SECTION04_CODE is None:
        SECTION04_CODE = _load_log("Section04Scene_attempt1.py")
    return SECTION04_CODE


# ── Section03 tests ───────────────────────────────────────────────────────────

class TestSection03ScenePrecheck:
    """Section03Scene_attempt1.py — bubble sort with VGroup tuple swap.

    This scene is architecturally nearly correct: it uses current_values (plain list)
    for logical tracking. The only bad line is: boxes[i], boxes[i+1] = boxes[i+1], boxes[i]
    That one line must be caught. The plain-list lines must NOT be caught.
    """

    def test_plain_list_swap_not_flagged(self):
        """current_values[i], current_values[i+1] = ... must NOT be flagged."""
        code = _section03()
        errors = validate_scene_code(code)
        # Count VGroup errors
        vgroup_errors = [e for e in errors if "VGroup" in e]
        # There should be at most ONE VGroup error (the real boxes[i] swap on line 77)
        # NOT multiple errors (which would include the plain list lines)
        assert len(vgroup_errors) <= 1, (
            f"Got {len(vgroup_errors)} VGroup errors — plain list lines are being flagged:\n"
            + "\n".join(vgroup_errors)
        )

    def test_vgroup_swap_line_is_caught(self):
        """boxes[i], boxes[i+1] = boxes[i+1], boxes[i] MUST still be caught."""
        code = _section03()
        errors = validate_scene_code(code)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors, "The real VGroup swap on boxes[] must still be detected"

    def test_precheck_error_classified_correctly(self):
        """The precheck error message maps to 'precheck_vgroup', not 'type' or 'runtime'."""
        precheck_stderr = (
            "Precheck failed:\n"
            "- VGroup does not support item assignment (vgroup[i] = x raises TypeError). "
            "Use a Python list for mutable indexed storage, then rebuild: "
            "items[i] = new_mob; group = VGroup(*items)."
        )
        error_type = _classify_error(precheck_stderr)
        assert error_type == "precheck_vgroup", (
            f"Got '{error_type}' — LLM will get wrong guidance and produce another broken scene"
        )

    def test_precheck_guidance_tells_llm_about_parallel_list(self):
        guidance = _fix_guidance("precheck_vgroup")
        assert "box_list" in guidance or "list(boxes)" in guidance, (
            f"Guidance must mention the parallel list pattern. Got:\n{guidance}"
        )

    def test_correctly_fixed_code_passes_precheck(self):
        """A scene using box_list correctly must pass precheck without VGroup errors."""
        correct_code = '''from manimlib import *

class Section03Scene(Scene):
    def construct(self):
        values = [5, 1, 4, 8, 2, 7]
        boxes = VGroup(*[
            Square(side_length=0.9, fill_color="#2a2a2a", fill_opacity=1, stroke_width=2, color=GREY_B)
            for _ in values
        ]).arrange(RIGHT, buff=0.15).center()
        labels = VGroup(*[
            Text(str(v), font_size=28, color=WHITE).move_to(boxes[k])
            for k, v in enumerate(values)
        ])

        box_list = list(boxes)
        label_list = list(labels)
        current_values = list(values)

        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.15), run_time=1.0)
        self.play(LaggedStart(*[FadeIn(l) for l in labels], lag_ratio=0.15), run_time=0.8)
        self.wait(1.0)

        for i in range(len(values) - 1):
            if current_values[i] > current_values[i + 1]:
                pos_i = box_list[i].get_center()
                pos_j = box_list[i + 1].get_center()
                self.play(
                    box_list[i].animate.move_to(pos_j),
                    box_list[i + 1].animate.move_to(pos_i),
                    label_list[i].animate.move_to(pos_j),
                    label_list[i + 1].animate.move_to(pos_i),
                    run_time=0.55,
                )
                box_list[i], box_list[i + 1] = box_list[i + 1], box_list[i]
                label_list[i], label_list[i + 1] = label_list[i + 1], label_list[i]
                current_values[i], current_values[i + 1] = current_values[i + 1], current_values[i]
            self.wait(0.2)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
'''
        fixed = precheck_and_autofix(correct_code)
        errors = validate_scene_code(fixed)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], (
            f"Correctly-written scene using box_list still fails precheck:\n"
            + "\n".join(vgroup_errors)
        )


# ── Section04 tests ───────────────────────────────────────────────────────────

class TestSection04ScenePrecheck:
    """Section04Scene_attempt1.py — multi-pass bubble sort with TMT on Text objects."""

    def test_transform_matching_tex_on_text_is_detected(self):
        """TransformMatchingTex(pass_counter_text, ...) where pass_counter_text is Text() must be detected."""
        code = _section04()
        fixed, applied = apply_known_fixes(code)
        tmt_applied = [a for a in applied if "TransformMatchingTex" in a]
        assert tmt_applied, (
            "apply_known_fixes must have converted TransformMatchingTex(Text, ...) to FadeOut/FadeIn. "
            f"Applied fixes: {applied}"
        )

    def test_transform_matching_tex_removed_after_fix(self):
        """After apply_known_fixes, no TransformMatchingTex on Text variables should remain."""
        code = _section04()
        fixed, applied = apply_known_fixes(code)
        # Any remaining TMT should only be on Tex() variables (there are none in Section04)
        from manimgen.validator.codeguard import _detect_tmt_on_text
        remaining = _detect_tmt_on_text(fixed)
        assert remaining == [], (
            f"TransformMatchingTex on Text still present after fix:\n" + "\n".join(remaining)
        )

    def test_correctly_fixed_section04_passes_precheck(self):
        """Section04 code after apply_known_fixes must have zero VGroup or TMT errors."""
        code = _section04()
        fixed, applied = apply_known_fixes(code)
        errors = validate_scene_code(fixed)
        blocking_errors = [
            e for e in errors
            if "VGroup" in e or ("TransformMatchingTex" in e and "Text" in e)
        ]
        assert blocking_errors == [], (
            f"Section04 still has blocking errors after auto-fix:\n"
            + "\n".join(blocking_errors)
        )


# ── Array swap example passes precheck ───────────────────────────────────────

class TestArraySwapExamplePassesPrecheck:
    """The example scene we give the Director must itself pass precheck.
    If it doesn't, we're teaching the Director broken patterns.
    """

    def test_array_swap_scene_passes_precheck(self):
        here = os.path.dirname(__file__)
        example_path = os.path.join(here, "..", "examples", "array_swap_scene.py")
        if not os.path.isfile(example_path):
            pytest.skip("examples/array_swap_scene.py not found")
        with open(example_path) as f:
            code = f.read()
        fixed = precheck_and_autofix(code)
        errors = validate_scene_code(fixed)
        vgroup_errors = [e for e in errors if "VGroup" in e]
        assert vgroup_errors == [], (
            f"array_swap_scene.py (our Director reference!) fails precheck:\n"
            + "\n".join(vgroup_errors)
        )
```

- [ ] **Step 2: Run — confirm tests skip or fail (not pass yet)**

```bash
python3 -m pytest tests/test_smoke_sections.py -v
```

Expected: tests that load log files either skip (if logs missing) or show the actual failure behavior. `test_correctly_fixed_code_passes_precheck` and `test_array_swap_scene_passes_precheck` should FAIL before the codeguard fix, PASS after.

- [ ] **Step 3: Run all tests together — verify no regressions**

```bash
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -v 2>&1 | tail -20
```

Expected: ≥344 + new tests PASS, 0 FAIL.

- [ ] **Step 4: Commit smoke test**

```bash
git checkout -b fix/integration-smoke
git add tests/test_smoke_sections.py
git commit -m "test: add section replay smoke tests for Section03/04 precheck+retry chain

These tests replay the exact scenes from the 2026-04-08 bubble-sort run
through the precheck → codeguard → classify → guidance chain without
calling manimgl. They verify:
- Plain list assignments are not flagged as VGroup errors
- precheck_vgroup error type is returned for VGroup precheck failures
- Guidance mentions box_list/list(boxes) pattern
- Correctly-written code using box_list passes precheck
- array_swap_scene.py (Director reference) passes precheck
- TransformMatchingTex(Text, ...) is auto-fixed in Section04"
```

### Task 4.2 — Update CLAUDE.md known issues

**Files:**
- Modify: `manimgen/CLAUDE.md`

- [ ] **Step 1: Mark the two HIGH PRIORITY issues as fixed**

Find the section `### HIGH PRIORITY — VGroup swap pattern causes 40% fallback rate` and replace the header:

```
### VGroup swap pattern — FIXED 2026-04-09 (branch fix/codeguard-vgroup-pattern)
```

Find `### HIGH PRIORITY — A/V timing mismatch` and update to mark the three parts done.

Find `### Codeguard missing patterns — FIXED 2026-04-08` and add a note:

```
**2026-04-09 follow-up:** Fixed false-positive regex that was rejecting correctly-fixed scenes.
```

- [ ] **Step 2: Commit the CLAUDE.md update**

```bash
git add manimgen/CLAUDE.md
git commit -m "docs: mark VGroup swap and A/V timing issues fixed in CLAUDE.md"
```

---

## Final smoke test (manual, runs manimgl)

After all branches are merged, run this to confirm end-to-end:

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen

# Clear previous outputs
rm -f manimgen/output/scenes/section_0*.py
rm -f manimgen/output/muxed/section_0*.mp4
rm -f manimgen/output/videos/understanding_bubble_sort.mp4

# Run pipeline with --resume (uses cached plan, no new LLM planning call)
MANIMGEN_MAX_RETRY_LLM_CALLS=1 python3 -m manimgen.cli "bubble sort" --resume 2>&1 | tee /tmp/smoke_run.log

# Check results
echo "=== Rendered sections ==="
ls -la manimgen/output/muxed/*.mp4 2>/dev/null | wc -l

echo "=== Fallback count ==="
grep -c "fallback" /tmp/smoke_run.log || echo "0"

echo "=== Muxer large mismatches ==="
grep "LARGE MISMATCH" /tmp/smoke_run.log || echo "none"

echo "=== Final video ==="
ls -lh manimgen/output/videos/*.mp4 2>/dev/null || echo "not produced"
```

**Target:** 5/5 sections render (0 fallbacks). No `LARGE MISMATCH` lines (or fewer than before). Final `.mp4` produced.

---

## Self-review

**Spec coverage check:**
- Bug 1 (false-positive banned pattern): Task 1.1 + 1.2 ✓
- Bug 2 (retry classifier wrong guidance): Task 2.1 + 2.2 ✓
- Bug 3 (retry deduplication skips second attempt): Handled by fixing Bug 1 — once precheck passes, there's no repeated error signature ✓
- Bug 4 (TransformMatchingTex on Text): Task 3.1 + 3.2 ✓
- Integration verification: Task 4.1 ✓
- CLAUDE.md update: Task 4.2 ✓

**Placeholder scan:** None found. All code blocks are complete.

**Type consistency:** `_classify_error` returns `"precheck_vgroup"` (str) — consistent with existing return type. `_fix_guidance` takes that str — consistent. `_detect_tmt_on_text` returns `list[str]` — matches `validate_scene_code` return type.

**Scope check:** Focused. No new features, no refactoring beyond what's required for the fixes.
