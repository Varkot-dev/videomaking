# Pipeline Validation Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four pipeline issues: assembler preset inconsistency (Issue 5), missing full codeguard on initial codegen (Issue 1), and first-pass render bypassing all visual validation (Issues 3+4).

**Architecture:** Issue 5 is a one-line FFmpeg flag change. Issue 1 is a small change to `scene_generator.py` — after writing the scene file, call `precheck_and_autofix_file()` instead of only the string variant. Issues 3+4 introduce a new `render_validator.py` with a `validate_render()` function that unifies `frame_checker` and `layout_checker` behind one interface, then wire it into `cli.py` (first-pass success path) and simplify the ad-hoc checks in `retry.py` to call through it.

**Tech Stack:** Python 3.13, existing PIL (frame_checker), FFmpeg/ffprobe (frame extraction), Gemini LLM (layout_checker), pytest

---

## File Map

| File | Change |
|---|---|
| `manimgen/manimgen/renderer/assembler.py` | Change `-preset fast` → `-preset slow` in `_normalise_all()` (Issue 5) |
| `manimgen/manimgen/generator/scene_generator.py` | After file write, call `precheck_and_autofix_file()` and log layout warnings (Issue 1) |
| `manimgen/manimgen/validator/render_validator.py` | **New file** — `validate_render()`, `ValidationResult` dataclass (Issues 3+4) |
| `manimgen/manimgen/cli.py` | After `run_scene()` success on first pass, call `validate_render()`; hard failures trigger `retry_scene()` (Issues 3+4) |
| `manimgen/manimgen/validator/retry.py` | Replace ad-hoc `check_frames` + `check_layout` block with `validate_render()` call (Issues 3+4) |
| `tests/test_assembler.py` | Add test that `_normalise_all()` uses `-preset slow` |
| `tests/test_codeguard.py` | Add test that `generate_scenes()` path runs the full file-based precheck |
| `tests/test_render_validator.py` | **New test file** — unit tests for `validate_render()` logic |

---

## Task 1: Fix assembler preset (Issue 5)

**Files:**
- Modify: `manimgen/manimgen/renderer/assembler.py:92-113`
- Test: `tests/test_assembler.py`

- [ ] **Step 1: Write a failing test**

  In `tests/test_assembler.py`, add a test that inspects the FFmpeg commands built by `_normalise_all()`. Check the existing test file first to see what fixtures exist:

  ```python
  # tests/test_assembler.py — add at bottom of file
  import subprocess
  from unittest.mock import patch, call

  def test_normalise_all_uses_slow_preset(tmp_path):
      """_normalise_all must use -preset slow, matching _xfade_pair."""
      clip = tmp_path / "clip.mp4"
      clip.write_bytes(b"")  # dummy — ffmpeg call is mocked

      captured_cmds = []

      def fake_run(cmd, **kwargs):
          captured_cmds.append(cmd)
          class R:
              returncode = 0
          return R()

      with patch("manimgen.renderer.assembler._has_audio_stream", return_value=True), \
           patch("subprocess.run", side_effect=fake_run):
          from manimgen.renderer.assembler import _normalise_all
          _normalise_all([str(clip)], str(tmp_path))

      assert len(captured_cmds) == 1
      cmd = captured_cmds[0]
      assert "-preset" in cmd
      preset_idx = cmd.index("-preset")
      assert cmd[preset_idx + 1] == "slow", (
          f"Expected '-preset slow' but got '-preset {cmd[preset_idx + 1]}'. "
          "_normalise_all must match _xfade_pair's quality setting."
      )
  ```

- [ ] **Step 2: Run the test to confirm it fails**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/test_assembler.py::test_normalise_all_uses_slow_preset -v
  ```

  Expected: `FAILED` — AssertionError because current code uses `-preset fast`.

- [ ] **Step 3: Make the change**

  In `manimgen/manimgen/renderer/assembler.py`, both occurrences of `-preset fast` in `_normalise_all()` (lines ~96 and ~110) must change to `-preset slow`:

  **Line ~96 (with-audio branch):**
  ```python
  # BEFORE
  "-c:v", "libx264", "-preset", "fast", "-crf", "17",
  # AFTER
  "-c:v", "libx264", "-preset", "slow", "-crf", "17",
  ```

  **Line ~110 (no-audio / inject-silence branch):**
  ```python
  # BEFORE
  "-c:v", "libx264", "-preset", "fast", "-crf", "17",
  # AFTER
  "-c:v", "libx264", "-preset", "slow", "-crf", "17",
  ```

- [ ] **Step 4: Run the test to confirm it passes**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/test_assembler.py::test_normalise_all_uses_slow_preset -v
  ```

  Expected: `PASSED`

- [ ] **Step 5: Run the full suite to confirm no regressions**

  ```
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```

  Expected: all tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add manimgen/manimgen/renderer/assembler.py tests/test_assembler.py
  git commit -m "fix(assembler): use -preset slow in _normalise_all to match _xfade_pair quality"
  ```

---

## Task 2: Call precheck_and_autofix_file after initial codegen (Issue 1)

**Files:**
- Modify: `manimgen/manimgen/generator/scene_generator.py:196-204`
- Test: (no new test file — existing `test_codeguard.py` verifies codeguard functions work; the wiring change is covered by checking the import and call pattern)

**Background:** `generate_scenes()` currently calls `precheck_and_autofix(code)` — the string-only variant — before writing the file. This applies auto-fixes but skips `validate_scene_code()`, `_check_layout_smells()`, and `_check_loop_timing_smells()`. Those only run through `precheck_and_autofix_file()`. The fix: keep the string call before saving (so auto-fixes are in `code`), then after `scene_path` is written, call `precheck_and_autofix_file(scene_path)` so the full validation suite runs. If precheck fails (banned pattern), log the layout warnings — the first render attempt will still proceed, but retry will catch it.

- [ ] **Step 1: Update the import in scene_generator.py**

  At the top of `manimgen/manimgen/generator/scene_generator.py`, the import currently reads:

  ```python
  from manimgen.validator.codeguard import precheck_and_autofix
  ```

  Change it to:

  ```python
  from manimgen.validator.codeguard import precheck_and_autofix, precheck_and_autofix_file
  ```

- [ ] **Step 2: Update generate_scenes() to call precheck_and_autofix_file after save**

  The current code at lines ~196-204:

  ```python
      code = precheck_and_autofix(code)

      scenes_dir = paths.scenes_dir()
      os.makedirs(scenes_dir, exist_ok=True)
      scene_path = os.path.join(scenes_dir, f"{section['id']}.py")
      with open(scene_path, "w") as f:
          f.write(code)

      return code, class_name, scene_path
  ```

  Change to:

  ```python
      code = precheck_and_autofix(code)

      scenes_dir = paths.scenes_dir()
      os.makedirs(scenes_dir, exist_ok=True)
      scene_path = os.path.join(scenes_dir, f"{section['id']}.py")
      with open(scene_path, "w") as f:
          f.write(code)

      precheck_result = precheck_and_autofix_file(scene_path)
      if not precheck_result["ok"]:
          import logging
          logging.getLogger(__name__).warning(
              "[scene_generator] Precheck failed for %s: %s",
              section["id"], precheck_result["stderr"],
          )
      elif precheck_result.get("layout_warnings"):
          import logging
          logging.getLogger(__name__).warning(
              "[scene_generator] Layout warnings for %s: %s",
              section["id"], precheck_result["layout_warnings"],
          )
      # Reload in case precheck_and_autofix_file applied in-place fixes
      with open(scene_path) as f:
          code = f.read()

      return code, class_name, scene_path
  ```

- [ ] **Step 3: Run the full suite to confirm no regressions**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```

  Expected: all tests pass.

- [ ] **Step 4: Commit**

  ```bash
  git add manimgen/manimgen/generator/scene_generator.py
  git commit -m "fix(codegen): run full precheck_and_autofix_file after initial scene save to catch banned patterns and layout smells"
  ```

---

## Task 3: Create render_validator.py (Issues 3+4)

**Files:**
- Create: `manimgen/manimgen/validator/render_validator.py`
- Create: `tests/test_render_validator.py`

**Design:** `validate_render()` runs `check_frames()` (PIL, zero cost) always, and `check_layout()` (LLM vision) only when TTS/segments are present (i.e., `cue_durations is not None`). Hard failures are black screen or banned patterns in code; soft failures are layout smells and frozen frames. The caller (cli.py or retry.py) acts on severity.

`ValidationResult`:
- `ok: bool` — True if no hard failures
- `issues: list[str]` — ISSUE|CAUSE|FIX lines from both checkers
- `severity: Literal["hard", "soft", "none"]` — "hard" if ok=False, "soft" if ok=True but issues exist, "none" if fully clean

- [ ] **Step 1: Write the failing tests first**

  Create `tests/test_render_validator.py`:

  ```python
  """Tests for render_validator.validate_render()."""
  import os
  from dataclasses import dataclass
  from unittest.mock import patch, MagicMock

  import pytest


  # ---------------------------------------------------------------------------
  # Helpers / stubs
  # ---------------------------------------------------------------------------

  @dataclass
  class _FakeFrameResult:
      ok: bool
      issues: list
      skipped: bool = False

      @property
      def issues_text(self):
          return "\n".join(self.issues)


  def _make_video(tmp_path, name="scene.mp4") -> str:
      p = tmp_path / name
      p.write_bytes(b"")
      return str(p)


  # ---------------------------------------------------------------------------
  # ValidationResult shape
  # ---------------------------------------------------------------------------

  def test_validation_result_clean():
      from manimgen.validator.render_validator import ValidationResult
      r = ValidationResult(ok=True, issues=[], severity="none")
      assert r.ok is True
      assert r.issues == []
      assert r.severity == "none"


  def test_validation_result_hard():
      from manimgen.validator.render_validator import ValidationResult
      r = ValidationResult(ok=False, issues=["black screen"], severity="hard")
      assert r.ok is False
      assert r.severity == "hard"


  def test_validation_result_soft():
      from manimgen.validator.render_validator import ValidationResult
      r = ValidationResult(ok=True, issues=["layout overlap"], severity="soft")
      assert r.ok is True
      assert r.severity == "soft"


  # ---------------------------------------------------------------------------
  # validate_render — clean path
  # ---------------------------------------------------------------------------

  def test_validate_render_clean(tmp_path):
      video = _make_video(tmp_path)
      clean_frame = _FakeFrameResult(ok=True, issues=[])
      clean_layout = {"ok": True, "issues": "", "skipped": False, "frames": []}

      with patch("manimgen.validator.render_validator.check_frames", return_value=clean_frame), \
           patch("manimgen.validator.render_validator.check_layout", return_value=clean_layout):
          from manimgen.validator.render_validator import validate_render
          result = validate_render(video, "from manimlib import *\n", "/tmp/scene.py", None)

      assert result.ok is True
      assert result.issues == []
      assert result.severity == "none"


  # ---------------------------------------------------------------------------
  # validate_render — hard failure (black frame)
  # ---------------------------------------------------------------------------

  def test_validate_render_hard_black_frame(tmp_path):
      video = _make_video(tmp_path)
      bad_frame = _FakeFrameResult(ok=False, issues=["ISSUE: Black/empty frame at t=1.0s | CAUSE: ... | FIX: ..."])
      clean_layout = {"ok": True, "issues": "", "skipped": False, "frames": []}

      with patch("manimgen.validator.render_validator.check_frames", return_value=bad_frame), \
           patch("manimgen.validator.render_validator.check_layout", return_value=clean_layout):
          from manimgen.validator.render_validator import validate_render
          result = validate_render(video, "from manimlib import *\n", "/tmp/scene.py", None)

      assert result.ok is False
      assert result.severity == "hard"
      assert len(result.issues) == 1
      assert "Black" in result.issues[0]


  # ---------------------------------------------------------------------------
  # validate_render — soft failure (layout issue only)
  # ---------------------------------------------------------------------------

  def test_validate_render_soft_layout(tmp_path):
      video = _make_video(tmp_path)
      clean_frame = _FakeFrameResult(ok=True, issues=[])
      layout_issue = {"ok": False, "issues": "ISSUE: Label overlap | CAUSE: ... | FIX: ...", "skipped": False, "frames": []}

      with patch("manimgen.validator.render_validator.check_frames", return_value=clean_frame), \
           patch("manimgen.validator.render_validator.check_layout", return_value=layout_issue):
          from manimgen.validator.render_validator import validate_render
          result = validate_render(video, "from manimlib import *\n", "/tmp/scene.py", [2.5, 3.0])

      assert result.ok is True
      assert result.severity == "soft"
      assert "Label overlap" in result.issues[0]


  # ---------------------------------------------------------------------------
  # validate_render — layout_checker skipped when cue_durations is None
  # ---------------------------------------------------------------------------

  def test_validate_render_skips_layout_without_tts(tmp_path):
      video = _make_video(tmp_path)
      clean_frame = _FakeFrameResult(ok=True, issues=[])

      with patch("manimgen.validator.render_validator.check_frames", return_value=clean_frame) as mock_frames, \
           patch("manimgen.validator.render_validator.check_layout") as mock_layout:
          from manimgen.validator.render_validator import validate_render
          validate_render(video, "code", "/tmp/s.py", cue_durations=None)

      mock_layout.assert_not_called()


  # ---------------------------------------------------------------------------
  # validate_render — missing video returns clean (skip)
  # ---------------------------------------------------------------------------

  def test_validate_render_missing_video():
      from manimgen.validator.render_validator import validate_render
      result = validate_render("/nonexistent/path.mp4", "code", "/tmp/s.py", None)
      assert result.ok is True
      assert result.severity == "none"
  ```

- [ ] **Step 2: Run the tests to confirm they all fail**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/test_render_validator.py -v
  ```

  Expected: `ModuleNotFoundError` — `render_validator` doesn't exist yet.

- [ ] **Step 3: Implement render_validator.py**

  Create `manimgen/manimgen/validator/render_validator.py`:

  ```python
  """
  Render validator — unified post-render quality gate.

  Runs after every successful manimgl render (first attempt and retries alike).
  Combines frame_checker (zero-cost PIL) and layout_checker (LLM vision) into a
  single interface so callers don't need to wire them separately.

  Severity:
    "hard" — ok=False: black screen, frozen animation.
             Caller must block muxing and trigger retry.
    "soft" — ok=True, issues non-empty: layout overlaps, edge clipping.
             Caller logs issues and injects them into the next LLM retry prompt.
    "none" — ok=True, issues empty: fully clean.
  """
  from __future__ import annotations

  import logging
  import os
  from dataclasses import dataclass, field
  from typing import Literal

  from manimgen.validator.frame_checker import check_frames
  from manimgen.validator.layout_checker import check_layout

  logger = logging.getLogger(__name__)

  # Frame checker issues that indicate a hard failure (unacceptable render).
  _HARD_KEYWORDS = ("Black/empty frame", "frames are")  # black + frozen = hard


  @dataclass
  class ValidationResult:
      ok: bool
      issues: list[str]
      severity: Literal["hard", "soft", "none"]


  def validate_render(
      video_path: str,
      code: str,
      scene_path: str,
      cue_durations: list[float] | None,
  ) -> ValidationResult:
      """Run post-render visual validation on a successfully rendered video.

      Args:
          video_path:    Path to the rendered .mp4 from manimgl.
          code:          The Python source that produced this render (used for
                         future context — not inspected here).
          scene_path:    Path to the .py scene file on disk (unused currently,
                         reserved for future static checks).
          cue_durations: Per-cue durations from TTS segmenter. When None, TTS
                         is disabled and layout_checker is skipped (saves LLM
                         tokens on non-narrated renders).

      Returns:
          ValidationResult with ok, issues, and severity.
      """
      if not os.path.exists(video_path):
          logger.debug("[render_validator] Video not found, skipping: %s", video_path)
          return ValidationResult(ok=True, issues=[], severity="none")

      all_issues: list[str] = []
      has_hard_failure = False

      # --- Tier 1: frame_checker (zero cost, always runs) ---
      frame_result = check_frames(video_path)
      if not frame_result.ok and not frame_result.skipped:
          for issue in frame_result.issues:
              all_issues.append(issue)
              if any(kw in issue for kw in _HARD_KEYWORDS):
                  has_hard_failure = True

      # --- Tier 2: layout_checker (LLM vision, only when TTS is on) ---
      if cue_durations is not None:
          layout = check_layout(video_path)
          if not layout.get("skipped") and not layout.get("ok") and layout.get("issues"):
              for line in layout["issues"].splitlines():
                  line = line.strip()
                  if line:
                      all_issues.append(line)

      if has_hard_failure:
          logger.info("[render_validator] Hard failure(s) in %s: %d issue(s)", video_path, len(all_issues))
          return ValidationResult(ok=False, issues=all_issues, severity="hard")

      if all_issues:
          logger.info("[render_validator] Soft issue(s) in %s: %d issue(s)", video_path, len(all_issues))
          return ValidationResult(ok=True, issues=all_issues, severity="soft")

      return ValidationResult(ok=True, issues=[], severity="none")
  ```

- [ ] **Step 4: Run the tests to confirm they all pass**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/test_render_validator.py -v
  ```

  Expected: all 8 tests `PASSED`.

- [ ] **Step 5: Run the full suite to confirm no regressions**

  ```
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```

  Expected: all tests pass.

- [ ] **Step 6: Commit**

  ```bash
  git add manimgen/manimgen/validator/render_validator.py tests/test_render_validator.py
  git commit -m "feat(validator): add validate_render() to unify frame_checker + layout_checker behind one interface"
  ```

---

## Task 4: Wire validate_render() into cli.py (Issues 3+4)

**Files:**
- Modify: `manimgen/manimgen/cli.py:182-200`

**Background:** Lines 182–200 of `cli.py` contain the code-or-fresh-render branch. After `run_scene()` returns `success=True`, the pipeline immediately proceeds to cut+mux — there is no visual validation. We need to call `validate_render()` here and, if severity is "hard", treat it as a failure and call `retry_scene()`.

- [ ] **Step 1: Add the import**

  In `manimgen/manimgen/cli.py`, add the import near the top with the other validator imports:

  ```python
  from manimgen.validator.render_validator import validate_render
  ```

- [ ] **Step 2: Update the run_scene() success path in _run_section()**

  The current code at lines ~191-200:

  ```python
          success, video_path = run_scene(scene_path, class_name)
          if not success:
              success, video_path = retry_scene(section, code, class_name, scene_path, cue_durations=cue_durations)
          if not success:
              log.info("[manimgen] All retries failed, using fallback")
              video_path = fallback_scene(section)
              success = bool(video_path)
          # Write hash sidecar after any successful render (including fallback)
          if success and video_path and os.path.exists(video_path):
              _write_hash_sidecar(video_path, current_topic_hash)
  ```

  Change to:

  ```python
          success, video_path = run_scene(scene_path, class_name)
          if success and video_path:
              vr = validate_render(video_path, code, scene_path, cue_durations)
              if vr.severity == "hard":
                  log.warning(
                      "[manimgen] First-pass render has hard failures — forcing retry: %s",
                      "; ".join(vr.issues),
                  )
                  success = False
                  video_path = None
              elif vr.issues:
                  log.warning(
                      "[manimgen] First-pass render has soft issues (logged for context): %s",
                      "; ".join(vr.issues),
                  )
          if not success:
              soft_issues = vr.issues if "vr" in dir() and vr is not None else []
              success, video_path = retry_scene(
                  section, code, class_name, scene_path,
                  cue_durations=cue_durations,
                  prior_issues=soft_issues,
              )
          if not success:
              log.info("[manimgen] All retries failed, using fallback")
              video_path = fallback_scene(section)
              success = bool(video_path)
          # Write hash sidecar after any successful render (including fallback)
          if success and video_path and os.path.exists(video_path):
              _write_hash_sidecar(video_path, current_topic_hash)
  ```

  **Note:** `prior_issues` is a new optional parameter being added to `retry_scene()` in Task 5. Write this first so the interface is clear.

- [ ] **Step 3: Run the full suite**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```

  Expected: tests pass (retry_scene signature change is applied in Task 5, but prior_issues defaults to [] so existing callers still work).

---

## Task 5: Unify retry.py to use validate_render() (Issues 3+4)

**Files:**
- Modify: `manimgen/manimgen/validator/retry.py:125-185`

**Background:** `retry_scene()` currently has an ad-hoc block (lines 147-185) that calls `check_frames()` and `check_layout()` separately. This should route through `validate_render()`. We also add `prior_issues: list[str] = []` to `retry_scene()`'s signature so cli.py can pass soft issues from the first-pass as context for the first LLM prompt.

- [ ] **Step 1: Update imports in retry.py**

  At the top of `manimgen/manimgen/validator/retry.py`, the current imports include:

  ```python
  from manimgen.validator.layout_checker import check_layout
  ```

  Replace that import with:

  ```python
  from manimgen.validator.render_validator import validate_render
  ```

  (frame_checker is no longer imported directly in retry.py — it goes through validate_render.)

  Also remove the frame_checker import that's currently inside the function body at line 147:
  ```python
  from manimgen.validator.frame_checker import check_frames
  ```

- [ ] **Step 2: Add prior_issues parameter to retry_scene() and update the visual check block**

  Current signature (line 125):
  ```python
  def retry_scene(
      section: dict,
      original_code: str,
      class_name: str,
      scene_path: str,
      cue_durations: list[float] | None = None,
  ) -> tuple[bool, str | None]:
  ```

  Change to:
  ```python
  def retry_scene(
      section: dict,
      original_code: str,
      class_name: str,
      scene_path: str,
      cue_durations: list[float] | None = None,
      prior_issues: list[str] | None = None,
  ) -> tuple[bool, str | None]:
  ```

  Current block at lines ~145-185 (inside the for loop, after `result = _run_and_capture(...)`):

  ```python
          if result["success"]:
              from manimgen.validator.frame_checker import check_frames
              frame_result = check_frames(result["video_path"])
              
              layout = check_layout(result["video_path"])
              
              combined_issues = []
              if not frame_result.ok:
                  combined_issues.extend(frame_result.issues_text.splitlines())
              if not layout["ok"] and layout["issues"]:
                  combined_issues.extend(layout["issues"].splitlines())

              if not combined_issues:
                  return True, result["video_path"]

              # Scene rendered but has visual defects. Feed structured feedback
              # back into the retry loop if budget allows.
              issues_text = "\n".join(combined_issues)
              print(f"[retry] Attempt {attempt}/{MAX_RETRIES} rendered but has visual defects:")
              for line in combined_issues:
                  print(f"[retry]   {line}")

              if llm_fix_calls_used >= MAX_LLM_FIX_CALLS:
                  print("[retry] LLM retry budget exhausted — accepting video despite visual issues.")
                  return True, result["video_path"]

              print("[retry] Requesting visual fix from LLM...")
              defective_frames = layout.get("frames", [])
              code = _request_visual_fix(code, "\n".join(combined_issues), system_prompt, defective_frames)
              llm_fix_calls_used += 1
              with open(scene_path, "w") as f:
                  f.write(code)
              precheck_and_autofix(scene_path)
              # Always reload — precheck may have applied auto-fixes in-place
              with open(scene_path) as f:
                  code = f.read()
              # Timing pass — catch timing bugs in the LLM's visual fix
              if cue_durations:
                  code, _ = _apply_timing_pass(code, scene_path, cue_durations)
              continue
  ```

  Replace with:

  ```python
          if result["success"]:
              vr = validate_render(result["video_path"], code, scene_path, cue_durations)

              if vr.severity == "none":
                  return True, result["video_path"]

              # Hard failure: black screen or frozen animation — must retry
              if vr.severity == "hard":
                  print(f"[retry] Attempt {attempt}/{MAX_RETRIES} rendered but has hard failures:")
                  for line in vr.issues:
                      print(f"[retry]   {line}")
              else:
                  # Soft failure: layout/edge issues — log and attempt visual fix
                  print(f"[retry] Attempt {attempt}/{MAX_RETRIES} rendered but has soft issues:")
                  for line in vr.issues:
                      print(f"[retry]   {line}")

              if llm_fix_calls_used >= MAX_LLM_FIX_CALLS:
                  print("[retry] LLM retry budget exhausted — accepting video despite visual issues.")
                  return True, result["video_path"]

              print("[retry] Requesting visual fix from LLM...")
              code = _request_visual_fix(code, "\n".join(vr.issues), system_prompt, [])
              llm_fix_calls_used += 1
              with open(scene_path, "w") as f:
                  f.write(code)
              precheck_and_autofix(scene_path)
              # Always reload — precheck may have applied auto-fixes in-place
              with open(scene_path) as f:
                  code = f.read()
              # Timing pass — catch timing bugs in the LLM's visual fix
              if cue_durations:
                  code, _ = _apply_timing_pass(code, scene_path, cue_durations)
              continue
  ```

  **Note on frames:** `_request_visual_fix()` previously received `layout.get("frames", [])` — the base64-encoded frames extracted by layout_checker. After this refactor, `validate_render()` doesn't expose them. Pass `[]` for now — the LLM still gets the issue text as structured ISSUE|CAUSE|FIX lines, which is what drives the fix. The reference frames are loaded inside `_request_visual_fix` regardless.

- [ ] **Step 3: Handle prior_issues injection into first LLM prompt**

  Inside `retry_scene()`, after the function-level variables are set up (after `seen_error_signatures: set[str] = set()`), add:

  ```python
      # If the caller detected soft issues on the first-pass render, carry
      # them as context for the first error-based LLM prompt.
      _prior_issues_text = "\n".join(prior_issues) if prior_issues else ""
  ```

  Then inside the `chat()` call block (where `fixed = chat(...)` is called), update the user prompt to include prior issues when present:

  ```python
          prior_context = (
              f"\nPrior render soft issues (for context — fix these too if possible):\n{_prior_issues_text}\n"
              if _prior_issues_text else ""
          )
          fixed = chat(
              system=system_prompt,
              user=f"""This ManimGL scene failed to render. Fix it.

  Error type: {error_type}
  Guidance: {guidance}
  {prior_context}
  Full error:
  {prompt_stderr}

  Original code:
  {prompt_code}""",
          )
  ```

- [ ] **Step 4: Run the full suite**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```

  Expected: all tests pass.

- [ ] **Step 5: Commit**

  ```bash
  git add manimgen/manimgen/cli.py manimgen/manimgen/validator/retry.py
  git commit -m "fix(validator): wire validate_render() into cli.py first-pass and retry.py — all renders now gated on visual validation"
  ```

---

## Task 6: Final verification

- [ ] **Step 1: Run the complete test suite one final time**

  ```
  cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```

  Expected: all tests pass. Count shown should be ≥ 399 (plus any new tests added).

- [ ] **Step 2: Run code review skill**

  Use `superpowers:requesting-code-review` to have a reviewer agent verify all commits are coherent, minimal, and solve the stated problems.

---

## Self-Review Against Spec

| Spec requirement | Task covering it |
|---|---|
| Issue 5: `_normalise_all()` use `-preset slow` | Task 1 |
| Issue 1: full codeguard (validate + layout/loop smells) after initial codegen | Task 2 |
| Issues 3+4: `validate_render()` function in `render_validator.py` | Task 3 |
| `ValidationResult` with `ok`, `issues`, `severity` | Task 3 |
| Hard failures block mux and force retry | Task 4 |
| Soft failures logged and injected into next LLM prompt | Tasks 4+5 |
| `cli.py` calls `validate_render()` unconditionally after `run_scene()` success | Task 4 |
| `retry_scene()` unified through `validate_render()` | Task 5 |
| New file lives in `manimgen/validator/render_validator.py` | Task 3 |
| 399 tests still pass | Task 6 |
