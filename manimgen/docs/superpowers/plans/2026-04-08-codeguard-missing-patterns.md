# Codeguard Missing Patterns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add 4 missing codeguard fixes that caused a 60% fallback rate in the "bubble sort" pipeline run, and correct a double-scale bug introduced by a hallucinated assumption about `Tex()`.

**Architecture:** All changes are in `codeguard.py` (two functions: `apply_known_fixes` and `apply_error_aware_fixes`) and `_BANNED_PATTERNS`. Each fix is a regex-based static transformation or a banned pattern entry. Tests live in `tests/test_codeguard.py`. No new files needed. CLAUDE.md gets one correction.

**Tech Stack:** Python 3.13, `re` module (already imported), `pytest` for tests.

---

## File Map

| File | Change |
|---|---|
| `manimgen/manimgen/validator/codeguard.py` | Add 3 entries to `_BANNED_PATTERNS`, add 3 regex rules to `apply_known_fixes()`, remove double-scale block from `apply_error_aware_fixes()` |
| `manimgen/tests/test_codeguard.py` | Add test classes for each new fix |
| `manimgen/CLAUDE.md` | Correct the false rule about `font_size=` on `Tex()` |

---

### Task 1: Fix 1 — Auto-wrap bare Mobject in `self.play()` with `ShowCreation()`

**Context:** `self.play(SurroundingRectangle(...))` crashes with `TypeError: Object SurroundingRectangle cannot be converted to an animation` because `scene.play()` calls `prepare_animation()` which only accepts `Animation` or `_AnimationBuilder` instances (confirmed in `manim/manimlib/animation/animation.py:216`). Same bug applies to `BackgroundRectangle`. Fix: auto-wrap in `ShowCreation()` and add a banned pattern so `validate_scene_code()` also catches the unfixed form.

**Files:**
- Modify: `manimgen/manimgen/validator/codeguard.py`
- Test: `manimgen/tests/test_codeguard.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `manimgen/tests/test_codeguard.py` (append after the existing `TestApplyKnownFixes` class):

```python
class TestSurroundingRectangleAutoWrap:

    def test_bare_surrounding_rect_wrapped_in_show_creation(self):
        code = "self.play(SurroundingRectangle(obj))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(SurroundingRectangle(obj))" in fixed
        assert any("ShowCreation" in a for a in applied)

    def test_bare_surrounding_rect_with_kwargs_wrapped(self):
        code = "self.play(SurroundingRectangle(obj, color=YELLOW))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(SurroundingRectangle(obj, color=YELLOW))" in fixed

    def test_already_wrapped_not_double_wrapped(self):
        code = "self.play(ShowCreation(SurroundingRectangle(obj)))"
        fixed, applied = apply_known_fixes(code)
        assert fixed == code
        assert applied == []

    def test_bare_background_rectangle_wrapped(self):
        code = "self.play(BackgroundRectangle(obj))"
        fixed, applied = apply_known_fixes(code)
        assert "ShowCreation(BackgroundRectangle(obj))" in fixed

    def test_validate_detects_bare_surrounding_rect(self):
        errors = validate_scene_code("self.play(SurroundingRectangle(obj))")
        assert any("ShowCreation" in e for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestSurroundingRectangleAutoWrap -v
```

Expected: 5 FAILs.

- [ ] **Step 3: Add banned pattern to `_BANNED_PATTERNS`**

In `manimgen/manimgen/validator/codeguard.py`, add to the `_BANNED_PATTERNS` list (after the `Circumscribe` entry):

```python
    (
        r"self\.play\(\s*(?:Surrounding|Background)Rectangle\s*\(",
        "Wrap SurroundingRectangle/BackgroundRectangle in ShowCreation(): "
        "self.play(ShowCreation(SurroundingRectangle(...))).",
    ),
```

- [ ] **Step 4: Add auto-fix rule to `apply_known_fixes()`**

In `manimgen/manimgen/validator/codeguard.py`, add these two entries to the `replacements` list in `apply_known_fixes()` (after the `Circumscribe -> FlashAround` entry):

```python
        # SurroundingRectangle/BackgroundRectangle are Mobjects, not Animations.
        # Wrap them in ShowCreation() so self.play() can accept them.
        (
            r"self\.play\(\s*(SurroundingRectangle\s*\([^)]*\))\s*\)",
            r"self.play(ShowCreation(\1))",
            "wrapped SurroundingRectangle in ShowCreation",
        ),
        (
            r"self\.play\(\s*(BackgroundRectangle\s*\([^)]*\))\s*\)",
            r"self.play(ShowCreation(\1))",
            "wrapped BackgroundRectangle in ShowCreation",
        ),
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestSurroundingRectangleAutoWrap -v
```

Expected: 5 PASSes.

- [ ] **Step 6: Run full test suite to check for regressions**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py -v
```

Expected: all existing tests still pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
git add manimgen/validator/codeguard.py tests/test_codeguard.py
git commit -m "fix: auto-wrap bare SurroundingRectangle/BackgroundRectangle in ShowCreation()"
```

---

### Task 2: Fix 2 — Strip outer `\text{}` wrapper from `Tex()` argument

**Context:** The LLM sometimes writes `Tex(r"\text{Bubble Sort}")` intending a plain label. In `align*` environment (which is what `Tex` uses), `\text{}` is valid amsmath — but when used as the **sole content** of the entire string, it's redundant and can crash if the inner content has bare special characters or the LLM accidentally mixes modes. The correct fix is to strip the outer `\text{}` wrapper and keep `Tex()` — this preserves LaTeX for math content (variables, proofs, discrete math) while removing the erroneous wrapper. We do NOT convert to `Text()` as that kills LaTeX capability.

**The regex must only match when `\text{}` is the outermost wrapper of the entire first argument** — not when it appears mid-expression like `Tex(r"f(x) = \text{identity}")` (that is valid and must be left alone).

**Files:**
- Modify: `manimgen/manimgen/validator/codeguard.py`
- Test: `manimgen/tests/test_codeguard.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `manimgen/tests/test_codeguard.py`:

```python
class TestTexTextOuterWrapperStrip:

    def test_strips_text_wrapper_single_quotes(self):
        code = r"label = Tex(r'\text{Bubble Sort}')"
        fixed, applied = apply_known_fixes(code)
        assert r"\text{" not in fixed
        assert r"Tex(r'Bubble Sort')" in fixed
        assert any("\\text{}" in a for a in applied)

    def test_strips_text_wrapper_double_quotes(self):
        code = r'label = Tex(r"\text{Step 1}")'
        fixed, applied = apply_known_fixes(code)
        assert r"\text{" not in fixed
        assert r'Tex(r"Step 1")' in fixed

    def test_does_not_strip_mid_expression(self):
        # \text{} used correctly inside an expression — must NOT be touched
        code = r'label = Tex(r"f(x) = \text{identity}")'
        fixed, applied = apply_known_fixes(code)
        assert r"\text{identity}" in fixed
        assert applied == []

    def test_does_not_strip_mixed_math_and_text(self):
        # Valid use: math with a text annotation
        code = r'label = Tex(r"\forall n \in \mathbb{N}, \text{n is positive}")'
        fixed, applied = apply_known_fixes(code)
        assert r"\text{n is positive}" in fixed
        assert applied == []

    def test_validate_detects_outer_text_wrapper(self):
        errors = validate_scene_code(r'Tex(r"\text{Bubble Sort}")')
        assert any("\\text{}" in e for e in errors)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestTexTextOuterWrapperStrip -v
```

Expected: 5 FAILs.

- [ ] **Step 3: Add banned pattern to `_BANNED_PATTERNS`**

In `manimgen/manimgen/validator/codeguard.py`, add to `_BANNED_PATTERNS`:

```python
    (
        r"""Tex\(\s*r?['"]\s*\\text\{[^}]*\}\s*['"]\s*\)""",
        r"Remove outer \text{} wrapper from Tex(): use Tex(r'content') not Tex(r'\text{content}'). "
        r"\text{} inside a longer expression is fine — only the bare outer wrapper is wrong.",
    ),
```

- [ ] **Step 4: Add auto-fix helper function**

In `manimgen/manimgen/validator/codeguard.py`, add this function after `_remove_font_kwarg_from_tex`:

```python
def _strip_outer_text_wrapper(code: str) -> tuple[str, str | None]:
    r"""Strip \text{...} when it is the sole content of a Tex() first argument.

    Matches:  Tex(r"\text{some label}")  or  Tex("\text{some label}")
    Leaves:   Tex(r"f(x) = \text{annotation}")  — \text mid-expression is valid.
    """
    # Match only when \text{...} is the ENTIRE string argument (anchored by ^ and $
    # inside the quotes). This avoids touching valid mid-expression uses.
    pattern = re.compile(
        r"""(Tex\(\s*r?)(['"]) \s* \\text\{([^}]*)\} \s* \2""",
        re.VERBOSE,
    )
    new, count = re.subn(pattern, r"\1\2\3\2", code)
    if count:
        return new, r"stripped outer \text{} wrapper from Tex() ({})".format(count)
    return code, None
```

- [ ] **Step 5: Wire `_strip_outer_text_wrapper` into `apply_known_fixes()`**

At the bottom of `apply_known_fixes()` in `codeguard.py`, just before the `return fixed, applied` line, add:

```python
    fixed, text_applied = _strip_outer_text_wrapper(fixed)
    if text_applied:
        applied.append(text_applied)
```

- [ ] **Step 6: Run tests to verify they pass**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestTexTextOuterWrapperStrip -v
```

Expected: 5 PASSes.

- [ ] **Step 7: Run full codeguard test suite**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py -v
```

Expected: all passing.

- [ ] **Step 8: Commit**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
git add manimgen/validator/codeguard.py tests/test_codeguard.py
git commit -m "fix: strip outer \\text{} wrapper from Tex() — keep LaTeX, remove erroneous wrapper"
```

---

### Task 3: Fix 3 — Ban `vgroup[i][j] = value` item assignment

**Context:** `VGroup` has no `__setitem__` (confirmed in `manim/manimlib/mobject/types/vectorized_mobject.py`). Index assignment raises `TypeError: 'VGroup' object does not support item assignment`. There is no safe auto-fix — the correct fix is architectural (use a Python list). We add it to `_BANNED_PATTERNS` so `validate_scene_code()` returns a precise error message for the retry LLM instead of just the cryptic Python traceback.

**Files:**
- Modify: `manimgen/manimgen/validator/codeguard.py`
- Test: `manimgen/tests/test_codeguard.py`

- [ ] **Step 1: Write the failing tests**

Add this class to `manimgen/tests/test_codeguard.py`:

```python
class TestVGroupItemAssignmentBan:

    def test_detects_double_index_assignment(self):
        errors = validate_scene_code("vgroup[i][j] = new_obj")
        assert any("VGroup" in e and "item assignment" in e for e in errors)

    def test_detects_single_index_assignment_on_vgroup(self):
        # vgroup[0] = x is equally invalid
        errors = validate_scene_code("cells[0] = Text('new')")
        assert any("VGroup" in e and "item assignment" in e for e in errors)

    def test_normal_index_read_not_flagged(self):
        # vgroup[i] on the right side of an expression is fine
        errors = validate_scene_code("obj = vgroup[0]")
        assert not any("item assignment" in e for e in errors)

    def test_apply_known_fixes_makes_no_change(self):
        # No autofix — structural problem, must surface as banned pattern only
        code = "vgroup[0][1] = new_mob"
        fixed, applied = apply_known_fixes(code)
        assert fixed == code
        assert applied == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestVGroupItemAssignmentBan -v
```

Expected: `test_detects_double_index_assignment` and `test_detects_single_index_assignment_on_vgroup` FAIL, others PASS.

- [ ] **Step 3: Add banned pattern to `_BANNED_PATTERNS`**

In `manimgen/manimgen/validator/codeguard.py`, add to `_BANNED_PATTERNS`:

```python
    (
        r"\w+\[.+?\]\s*=\s*\S",
        "VGroup does not support item assignment (vgroup[i] = x raises TypeError). "
        "Use a Python list for mutable indexed storage, then rebuild the VGroup: "
        "items[i] = new_mob; group = VGroup(*items).",
    ),
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestVGroupItemAssignmentBan -v
```

Expected: 4 PASSes.

- [ ] **Step 5: Run full codeguard test suite**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py -v
```

Expected: all passing. If `test_normal_index_read_not_flagged` unexpectedly fails, tighten the pattern to `r"\w+\[.+?\]\s*=(?!=)\s*\S"` (exclude `==`).

- [ ] **Step 6: Commit**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
git add manimgen/validator/codeguard.py tests/test_codeguard.py
git commit -m "fix: ban VGroup item assignment — surfaces precise error for retry LLM"
```

---

### Task 4: Fix 4 — Remove double-scale bug for `font_size=` on `Tex()`

**Context:** `Tex.__init__` accepts `font_size: int = 48` as a real keyword argument and handles it internally with `self.scale(get_tex_mob_scale_factor() * font_size)` (confirmed in `manim/manimlib/mobject/svg/tex_mobject.py:77`). The existing `apply_error_aware_fixes()` code incorrectly converts `Tex("x", font_size=48)` → `Tex("x").scale(1.5)`, which double-scales (Tex already scaled itself, then the fix scales again). This block must be removed. CLAUDE.md also has a wrong rule that must be corrected.

**Files:**
- Modify: `manimgen/manimgen/validator/codeguard.py`
- Modify: `manimgen/CLAUDE.md`
- Test: `manimgen/tests/test_codeguard.py`

- [ ] **Step 1: Write the failing test that documents the correct behavior**

Add this class to `manimgen/tests/test_codeguard.py`:

```python
class TestFontSizeOnTexNotDoubleScaled:

    def test_font_size_on_tex_is_left_alone(self):
        # font_size= is a valid Tex() kwarg — must NOT be converted to .scale()
        code = 'label = Tex(r"x^2 + y^2", font_size=48)'
        fixed, applied = apply_error_aware_fixes(
            code,
            "TypeError: Animation.__init__() got an unexpected keyword argument 'font_size'"
        )
        # The fix should NOT produce .scale() — font_size is valid on Tex()
        assert ".scale(" not in fixed
        assert "font_size=48" in fixed

    def test_font_size_kwarg_not_stripped_from_tex(self):
        # Also verify apply_known_fixes does not touch it
        code = 'eq = Tex(r"\\frac{1}{2}", font_size=36)'
        fixed, applied = apply_known_fixes(code)
        assert "font_size=36" in fixed
        assert applied == []

    def test_unexpected_other_kwarg_still_stripped(self):
        # Other unexpected kwargs must still be stripped by error-aware path
        code = "FadeIn(obj, bogus_param=True)"
        stderr = "TypeError: Animation.__init__() got an unexpected keyword argument 'bogus_param'"
        fixed, applied = apply_error_aware_fixes(code, stderr)
        assert "bogus_param" not in fixed
        assert any("bogus_param" in a for a in applied)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestFontSizeOnTexNotDoubleScaled -v
```

Expected: `test_font_size_on_tex_is_left_alone` FAIL (the old code converts it to `.scale()`), others PASS.

- [ ] **Step 3: Remove the double-scale block from `apply_error_aware_fixes()`**

In `manimgen/manimgen/validator/codeguard.py`, find the block inside `apply_error_aware_fixes()` that handles `font_size` (around line 185–210). It looks like this:

```python
            if bad_kw == "font_size":
                # font_size is valid on Text() but not Tex()/MathTex(). Convert to
                # .scale() so sizing is preserved: Tex("x", font_size=48) -> Tex("x").scale(1.5)
                # 32 = baseline, scale = font_size / 32
                def _fs_to_scale(m: re.Match) -> str:
                    ...
                new_fixed = re.sub(
                    r",?\s*font_size\s*=\s*([0-9]+(?:\.[0-9]+)?)\s*(\))",
                    lambda m: f").scale({round(float(m.group(1)) / 32, 2)})",
                    fixed,
                )
                count = fixed != new_fixed
                if count:
                    applied.append(f"font_size= on Tex -> .scale() ({count})")
                    fixed = new_fixed
                else:
                    # Fallback: just strip it
                    new_fixed, count = re.subn(rf",?\s*{bad_kw}\s*=\s*[^,\)\n]+", "", fixed)
                    if count:
                        applied.append(f"removed unexpected kwarg '{bad_kw}' ({count})")
                        fixed = new_fixed
            else:
```

Replace the entire `if bad_kw == "font_size": ... else:` block with just the `else` branch body (removing the `else:` wrapper too), so the function falls through to the generic kwarg-strip for any unexpected kwarg:

```python
            # font_size= is valid on Tex() — do not strip or convert it.
            # Only strip genuinely unexpected kwargs.
            if bad_kw != "font_size":
                new_fixed, count = re.subn(rf",?\s*{bad_kw}\s*=\s*[^,\)\n]+", "", fixed)
                if count:
                    applied.append(f"removed unexpected kwarg '{bad_kw}' ({count})")
                    fixed = new_fixed
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py::TestFontSizeOnTexNotDoubleScaled -v
```

Expected: 3 PASSes.

- [ ] **Step 5: Run full codeguard test suite**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/test_codeguard.py -v
```

Expected: all passing. If the old `test_unexpected_kwarg_from_stderr` test fails, check it — it tests a non-`font_size` kwarg so it should still pass.

- [ ] **Step 6: Correct the wrong rule in `CLAUDE.md`**

In `manimgen/CLAUDE.md`, find the line in the ManimGL vs ManimCommunity table:

```
| `Tex(...)` | `MathTex(...)` |
```

And find the "Key rules for development" section, rule 2:

```
2. **ManimGL `Tex()` does NOT accept `font_size=`** — only `Text()` does. codeguard converts it to `.scale()`.
```

Replace rule 2 with:

```
2. **ManimGL `Tex()` accepts `font_size=`** — it is a real parameter (default 48) handled internally. `Text()` also accepts it. Do NOT convert `Tex(r"x", font_size=48)` to `.scale()` — that double-scales.
```

- [ ] **Step 7: Run the full test suite**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -v
```

Expected: all tests pass. Count should be ≥ 314 (the baseline) plus the new tests added in Tasks 1–4.

- [ ] **Step 8: Commit**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
git add manimgen/validator/codeguard.py tests/test_codeguard.py CLAUDE.md
git commit -m "fix: remove double-scale bug for font_size= on Tex(), correct CLAUDE.md rule"
```

---

## Self-Review

**Spec coverage check:**

| Spec requirement | Task |
|---|---|
| `self.play(SurroundingRectangle(...))` → `ShowCreation` wrap | Task 1 |
| `Tex(r"\text{...}")` outer wrapper strip, keep LaTeX | Task 2 |
| `vgroup[i][j] = value` banned, precise error for retry LLM | Task 3 |
| `font_size=` double-scale removed, CLAUDE.md corrected | Task 4 |
| Tests for all 4 fixes in `test_codeguard.py` | All tasks |

**No gaps found.**

**Placeholder scan:** No TBDs, all code blocks are complete, all commands have expected output.

**Type consistency:** `apply_known_fixes` and `apply_error_aware_fixes` signatures unchanged throughout. `_strip_outer_text_wrapper` follows the existing `_fix_color_gradient_int_cast` / `_remove_font_kwarg_from_tex` helper pattern exactly.

**Edge case noted for Task 3:** The banned pattern `r"\w+\[.+?\]\s*=\s*\S"` could match augmented assignment (`+=`, `//=`) or comparisons if there's no space. The `(?!=)` negative lookahead note in Step 5 handles this if it arises.
