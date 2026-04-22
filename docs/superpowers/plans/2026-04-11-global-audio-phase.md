# Global Audio Phase + Director Context Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the pipeline so all TTS audio is generated upfront for the entire video before any section starts rendering, and the Director receives global video context (position, total duration, pacing) when writing each section's scene code.

**Architecture:** Split `main()` in `cli.py` into two explicit phases: Phase 1 runs TTS for all sections and builds a global overview dict; Phase 2 iterates sections, passing pre-computed audio and the overview to `_run_section()`. `scene_generator.generate_scenes()` gains an optional `overview` parameter that injects a short context block into the Director's user message.

**Tech Stack:** Python 3.13, pytest (unittest.mock), existing manimgen package at `manimgen/manimgen/manimgen/`

---

## File Map

| File | Change |
|---|---|
| `manimgen/manimgen/manimgen/cli.py` | Add `_build_overview()`, split `main()` into two phases, update `_run_section()` signature to accept `section_audio` and `overview` |
| `manimgen/manimgen/manimgen/generator/scene_generator.py` | Add `overview` optional param to `generate_scenes()` and `_build_user_message()`, inject context block |
| `manimgen/manimgen/tests/test_global_audio_phase.py` | New test file for all cli.py Phase 1/2 behaviour |
| `manimgen/manimgen/tests/test_overview_injection.py` | New test file for scene_generator.py overview injection |

**Do NOT touch:** `codeguard.py`, `retry.py`, `assembler.py`, `timing_verifier.py`, `render_validator.py`, or any existing test file.

---

## Task 1: Write failing tests for `_build_overview()`

**Files:**
- Create: `manimgen/manimgen/tests/test_global_audio_phase.py`

- [ ] **Step 1: Create the test file with `_build_overview` tests**

```python
# manimgen/manimgen/tests/test_global_audio_phase.py
"""
Tests for global audio Phase 1 in cli.py.

Zero LLM calls. Zero subprocess calls. All TTS is mocked.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from unittest.mock import MagicMock, patch, call


# ---------------------------------------------------------------------------
# _build_overview tests
# ---------------------------------------------------------------------------

class TestBuildOverview:

    def _make_lesson_plan(self):
        return {
            "title": "Gradient Descent",
            "sections": [
                {"id": "section_01", "title": "Introduction"},
                {"id": "section_02", "title": "The Gradient"},
                {"id": "section_03", "title": "Convergence"},
            ],
        }

    def _make_all_section_audio(self):
        return {
            "section_01": {
                "audio_path": "/tmp/section_01.mp3",
                "timestamps": [],
                "audio_duration": 94.2,
                "segments": [],
                "audio_slices": [],
                "cue_durations": [30.0, 64.2],
            },
            "section_02": {
                "audio_path": "/tmp/section_02.mp3",
                "timestamps": [],
                "audio_duration": 132.0,
                "segments": [],
                "audio_slices": [],
                "cue_durations": [45.0, 87.0],
            },
            "section_03": {
                "audio_path": "/tmp/section_03.mp3",
                "timestamps": [],
                "audio_duration": 61.1,
                "segments": [],
                "audio_slices": [],
                "cue_durations": [61.1],
            },
        }

    def test_total_duration_is_sum_of_all_sections(self):
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = self._make_all_section_audio()
        overview = _build_overview(plan, audio)
        assert abs(overview["total_duration"] - (94.2 + 132.0 + 61.1)) < 0.01

    def test_n_sections_matches_plan(self):
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = self._make_all_section_audio()
        overview = _build_overview(plan, audio)
        assert overview["n_sections"] == 3

    def test_section_positions_are_1_of_n(self):
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = self._make_all_section_audio()
        overview = _build_overview(plan, audio)
        positions = [s["position"] for s in overview["sections"]]
        assert positions == ["1 of 3", "2 of 3", "3 of 3"]

    def test_section_durations_match_audio(self):
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = self._make_all_section_audio()
        overview = _build_overview(plan, audio)
        assert abs(overview["sections"][0]["duration"] - 94.2) < 0.01
        assert abs(overview["sections"][1]["duration"] - 132.0) < 0.01
        assert abs(overview["sections"][2]["duration"] - 61.1) < 0.01

    def test_section_n_cues_matches_cue_durations(self):
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = self._make_all_section_audio()
        overview = _build_overview(plan, audio)
        assert overview["sections"][0]["n_cues"] == 2
        assert overview["sections"][1]["n_cues"] == 2
        assert overview["sections"][2]["n_cues"] == 1

    def test_pacing_notes_is_string(self):
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = self._make_all_section_audio()
        overview = _build_overview(plan, audio)
        assert isinstance(overview["pacing_notes"], str)

    def test_section_missing_from_audio_still_included(self):
        """A section with no TTS audio should still appear in overview with duration 0."""
        from manimgen.cli import _build_overview
        plan = self._make_lesson_plan()
        audio = {}  # no audio for any section
        overview = _build_overview(plan, audio)
        assert overview["n_sections"] == 3
        assert abs(overview["total_duration"] - 0.0) < 0.01
        assert overview["sections"][0]["duration"] == 0.0


# ---------------------------------------------------------------------------
# _run_section uses precomputed audio (no internal TTS call)
# ---------------------------------------------------------------------------

class TestRunSectionUsesPrecomputedAudio:

    def _make_section(self):
        return {
            "id": "section_01",
            "title": "Test",
            "narration": "Hello world.",
            "cue_word_indices": [0],
            "cues": [{"index": 0, "visual": "Circle appears"}],
        }

    @patch("manimgen.cli.generate_scenes")
    @patch("manimgen.cli.run_scene")
    @patch("manimgen.cli.fallback_scene")
    @patch("manimgen.cli.retry_scene")
    @patch("manimgen.cli._find_rendered_video")
    @patch("manimgen.cli._render_is_fresh")
    def test_does_not_call_tts_when_section_audio_provided(
        self,
        mock_fresh, mock_find, mock_retry, mock_fallback, mock_run_scene, mock_gen
    ):
        from manimgen.cli import _run_section
        mock_fresh.return_value = False
        mock_find.return_value = None
        mock_gen.return_value = ("from manimlib import *\n\nclass Section01Scene(Scene):\n    def construct(self): self.wait(1)", "Section01Scene", "/tmp/section_01.py")
        mock_run_scene.return_value = (False, None)
        mock_retry.return_value = (False, None)
        mock_fallback.return_value = "/tmp/fallback.mp4"

        from manimgen.planner.segmenter import CueSegment
        seg = CueSegment(start=0.0, end=5.0, duration=5.0, cue_index=0)

        section_audio = {
            "audio_path": "/tmp/section_01.mp3",
            "timestamps": [],
            "audio_duration": 5.0,
            "segments": [seg],
            "audio_slices": [],
            "cue_durations": [5.0],
        }

        with patch("manimgen.cli._run_tts_for_section") as mock_tts:
            _run_section(
                self._make_section(), 1, tts_on=True,
                current_topic_hash="abc12345",
                section_audio=section_audio,
                overview=None,
            )
            mock_tts.assert_not_called()

    @patch("manimgen.cli.generate_scenes")
    @patch("manimgen.cli.run_scene")
    @patch("manimgen.cli.fallback_scene")
    @patch("manimgen.cli.retry_scene")
    @patch("manimgen.cli._find_rendered_video")
    @patch("manimgen.cli._render_is_fresh")
    def test_tts_off_still_works_without_section_audio(
        self,
        mock_fresh, mock_find, mock_retry, mock_fallback, mock_run_scene, mock_gen
    ):
        from manimgen.cli import _run_section
        mock_fresh.return_value = False
        mock_find.return_value = None
        mock_gen.return_value = ("from manimlib import *\n\nclass Section01Scene(Scene):\n    def construct(self): self.wait(1)", "Section01Scene", "/tmp/section_01.py")
        mock_run_scene.return_value = (False, None)
        mock_retry.return_value = (False, None)
        mock_fallback.return_value = "/tmp/fallback.mp4"

        result = _run_section(
            self._make_section(), 1, tts_on=False,
            current_topic_hash="abc12345",
            section_audio=None,
            overview=None,
        )
        # Should complete without error; fallback path returns a video
        assert result == ["/tmp/fallback.mp4"]


# ---------------------------------------------------------------------------
# Phase 1: all TTS runs before any codegen
# ---------------------------------------------------------------------------

class TestAllTTSBeforeCodegen:

    def _make_plan(self):
        return {
            "title": "Test",
            "_topic_hash": "abc12345",
            "sections": [
                {
                    "id": "section_01",
                    "title": "S1",
                    "narration": "Hello world.",
                    "cue_word_indices": [0],
                    "cues": [{"index": 0, "visual": "Circle"}],
                },
                {
                    "id": "section_02",
                    "title": "S2",
                    "narration": "Goodbye world.",
                    "cue_word_indices": [0],
                    "cues": [{"index": 0, "visual": "Square"}],
                },
            ],
        }

    def test_all_tts_runs_before_any_codegen(self):
        """TTS must be called for ALL sections before generate_scenes is called for section 1."""
        call_order = []

        def fake_tts(section, idx):
            call_order.append(("tts", section["id"]))
            return None  # TTS result None means no audio — pipeline continues

        def fake_codegen(section, cue_durations=None, overview=None):
            call_order.append(("codegen", section["id"]))
            return ("from manimlib import *\n\nclass S(Scene):\n    def construct(self): self.wait(1)", "S", "/tmp/s.py")

        with patch("manimgen.cli._run_tts_for_section", side_effect=fake_tts), \
             patch("manimgen.cli.generate_scenes", side_effect=fake_codegen), \
             patch("manimgen.cli.run_scene", return_value=(False, None)), \
             patch("manimgen.cli.retry_scene", return_value=(False, None)), \
             patch("manimgen.cli.fallback_scene", return_value="/tmp/fallback.mp4"), \
             patch("manimgen.cli._find_rendered_video", return_value=None), \
             patch("manimgen.cli._render_is_fresh", return_value=False), \
             patch("manimgen.cli.assemble_video", return_value="/tmp/final.mp4"), \
             patch("manimgen.cli.plan_lesson", return_value=self._make_plan()), \
             patch("manimgen.cli.parse_input", return_value="test topic"), \
             patch("manimgen.cli._load_config", return_value={"tts": {"enabled": True}}), \
             patch("manimgen.cli.os.makedirs"), \
             patch("builtins.open", MagicMock()), \
             patch("manimgen.cli.json.dump"), \
             patch("manimgen.cli.json.load", return_value=self._make_plan()), \
             patch("manimgen.cli.os.path.exists", return_value=False):

            import sys
            with patch.object(sys, "argv", ["manimgen", "test topic"]):
                try:
                    from manimgen.cli import main
                    main()
                except SystemExit:
                    pass
                except Exception:
                    pass  # pipeline errors are OK; we only care about call order

        tts_calls = [i for i, (kind, _) in enumerate(call_order) if kind == "tts"]
        codegen_calls = [i for i, (kind, _) in enumerate(call_order) if kind == "codegen"]

        assert len(tts_calls) > 0, "TTS was never called"
        assert len(codegen_calls) > 0, "codegen was never called"
        # Last TTS call must happen before first codegen call
        assert max(tts_calls) < min(codegen_calls), (
            f"Codegen started before all TTS finished. Call order: {call_order}"
        )
```

- [ ] **Step 2: Run the tests to confirm they all fail (functions don't exist yet)**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/test_global_audio_phase.py -v 2>&1 | head -60
```

Expected: `ImportError` or `AttributeError: module 'manimgen.cli' has no attribute '_build_overview'`

---

## Task 2: Implement `_build_overview()` in `cli.py`

**Files:**
- Modify: `manimgen/manimgen/manimgen/cli.py` — add `_build_overview()` function after `_run_tts_for_section()`

- [ ] **Step 1: Add `_build_overview()` to `cli.py` after line 60 (after `_run_tts_for_section`)**

Insert this function between `_run_tts_for_section` and `_muxed_path_for`:

```python
def _build_overview(lesson_plan: dict, all_section_audio: dict) -> dict:
    """Build a global video overview from pre-computed per-section audio data.

    Returns a dict with total_duration, n_sections, per-section metadata,
    and a pacing_notes string highlighting the longest section.
    """
    sections = lesson_plan.get("sections", [])
    n = len(sections)

    section_entries = []
    for i, section in enumerate(sections, start=1):
        section_id = section.get("id", f"section_{i:02d}")
        audio = all_section_audio.get(section_id, {})
        dur = audio.get("audio_duration", 0.0)
        cue_durations = audio.get("cue_durations", [])
        section_entries.append({
            "id": section_id,
            "title": section.get("title", ""),
            "duration": dur,
            "n_cues": len(cue_durations),
            "position": f"{i} of {n}",
        })

    total = sum(e["duration"] for e in section_entries)

    # Build a short pacing note about the longest section
    pacing_notes = ""
    if section_entries:
        longest = max(section_entries, key=lambda e: e["duration"])
        if longest["duration"] > 0:
            mins, secs = divmod(int(longest["duration"]), 60)
            pacing_notes = f"Section '{longest['title']}' is longest at {mins}m{secs:02d}s"

    return {
        "total_duration": total,
        "n_sections": n,
        "sections": section_entries,
        "pacing_notes": pacing_notes,
    }
```

- [ ] **Step 2: Run the `_build_overview` tests only**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/test_global_audio_phase.py::TestBuildOverview -v
```

Expected: All `TestBuildOverview` tests PASS.

---

## Task 3: Update `_run_section()` signature and remove internal TTS

**Files:**
- Modify: `manimgen/manimgen/manimgen/cli.py` — `_run_section()` function (lines 120–237)

- [ ] **Step 1: Change `_run_section()` signature and body**

Replace the current `_run_section` signature and the TTS block inside it. The new function accepts `section_audio` (pre-computed dict or None) and `overview` (passed through to `generate_scenes`).

Find the current signature:
```python
def _run_section(
    section: dict,
    idx: int,
    tts_on: bool,
    current_topic_hash: str,
) -> list[str]:
```

Replace with:
```python
def _run_section(
    section: dict,
    idx: int,
    tts_on: bool,
    current_topic_hash: str,
    section_audio: dict | None = None,
    overview: dict | None = None,
) -> list[str]:
```

Then find the TTS block inside `_run_section` (currently lines ~138–160):
```python
    if tts_on:
        tts_result = _run_tts_for_section(section, idx)
        if tts_result:
            from manimgen.planner.segmenter import compute_segments
            from manimgen.renderer.audio_slicer import slice_audio

            audio_path, timestamps, audio_duration = tts_result
            cue_word_indices = section.get("cue_word_indices", [0])
            segments = compute_segments(timestamps, cue_word_indices, audio_duration)
            log.info("[manimgen] %d cue segment(s) for this section", len(segments))

            # Skip entire section if all cues already muxed
            if _all_cues_muxed(section, idx, len(segments)):
                log.info("[manimgen] All cues already muxed, skipping section")
                return [_muxed_path_for(section, idx, i) for i in range(len(segments))]

            audio_slices = slice_audio(
                audio_path, segments,
                output_dir=paths.audio_dir(),
                section_id=section_id,
            )
            log.info("[manimgen] Audio slices: %s", [os.path.basename(p) for p in audio_slices])
```

Replace with:
```python
    if tts_on and section_audio:
        segments = section_audio.get("segments", [])
        audio_slices = section_audio.get("audio_slices", [])
        log.info("[manimgen] %d cue segment(s) for this section (pre-computed)", len(segments))

        # Skip entire section if all cues already muxed
        if segments and _all_cues_muxed(section, idx, len(segments)):
            log.info("[manimgen] All cues already muxed, skipping section")
            return [_muxed_path_for(section, idx, i) for i in range(len(segments))]
```

Then find the `generate_scenes` call (currently):
```python
        code, class_name, scene_path = generate_scenes(section, cue_durations=cue_durations)
```

Replace with:
```python
        code, class_name, scene_path = generate_scenes(section, cue_durations=cue_durations, overview=overview)
```

- [ ] **Step 2: Run the `_run_section` tests**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/test_global_audio_phase.py::TestRunSectionUsesPrecomputedAudio -v
```

Expected: Both tests PASS.

---

## Task 4: Split `main()` into Phase 1 and Phase 2

**Files:**
- Modify: `manimgen/manimgen/manimgen/cli.py` — `main()` function (lines 244–300)

- [ ] **Step 1: Replace the section loop in `main()` with two-phase logic**

Find the current section loop in `main()`:
```python
    rendered_videos: list[str] = []
    for idx, section in enumerate(lesson_plan["sections"], start=1):
        rendered_videos.extend(
            _run_section(section, idx, tts_on, current_topic_hash)
        )
```

Replace with:
```python
    # -----------------------------------------------------------------------
    # Phase 1: Generate TTS for ALL sections upfront, build global overview
    # -----------------------------------------------------------------------
    all_section_audio: dict = {}
    if tts_on:
        for idx, section in enumerate(lesson_plan["sections"], start=1):
            section_id = section.get("id", f"section_{idx:02d}")
            tts_result = _run_tts_for_section(section, idx)
            if tts_result:
                from manimgen.planner.segmenter import compute_segments
                from manimgen.renderer.audio_slicer import slice_audio

                audio_path, timestamps, audio_duration = tts_result
                cue_word_indices = section.get("cue_word_indices", [0])
                segments = compute_segments(timestamps, cue_word_indices, audio_duration)
                audio_slices = slice_audio(
                    audio_path, segments,
                    output_dir=paths.audio_dir(),
                    section_id=section_id,
                )
                all_section_audio[section_id] = {
                    "audio_path": audio_path,
                    "timestamps": timestamps,
                    "audio_duration": audio_duration,
                    "segments": segments,
                    "audio_slices": audio_slices,
                    "cue_durations": [seg.duration for seg in segments],
                }

    overview = _build_overview(lesson_plan, all_section_audio)

    # -----------------------------------------------------------------------
    # Phase 2: Codegen + render each section using pre-computed audio
    # -----------------------------------------------------------------------
    rendered_videos: list[str] = []
    for idx, section in enumerate(lesson_plan["sections"], start=1):
        section_id = section.get("id", f"section_{idx:02d}")
        section_audio = all_section_audio.get(section_id)
        rendered_videos.extend(
            _run_section(section, idx, tts_on, current_topic_hash, section_audio, overview)
        )
```

- [ ] **Step 2: Run the full Phase 1/2 ordering test**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/test_global_audio_phase.py::TestAllTTSBeforeCodegen -v
```

Expected: PASS.

- [ ] **Step 3: Run the full test suite to confirm nothing is broken**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

Expected: All tests pass (463+ passing, 0 failed).

- [ ] **Step 4: Commit cli.py changes**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
git add manimgen/cli.py tests/test_global_audio_phase.py
git commit -m "feat(cli): split main() into Phase 1 (global TTS) and Phase 2 (per-section codegen)"
```

---

## Task 5: Write failing tests for `scene_generator.py` overview injection

**Files:**
- Create: `manimgen/manimgen/tests/test_overview_injection.py`

- [ ] **Step 1: Create the test file**

```python
# manimgen/manimgen/tests/test_overview_injection.py
"""
Tests for overview context injection in generate_scenes() / _build_user_message().

Zero LLM calls. All chat() calls are mocked.
"""
import os
import sys
import textwrap
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock


class TestOverviewInjection:

    def _make_section(self):
        return {
            "id": "section_02",
            "title": "The Gradient",
            "narration": "Hello world.",
            "cue_word_indices": [0],
            "cues": [{"index": 0, "visual": "Axes appear"}],
        }

    def _make_overview(self):
        return {
            "total_duration": 287.3,
            "n_sections": 3,
            "sections": [
                {"id": "section_01", "title": "Intro", "duration": 94.2, "n_cues": 2, "position": "1 of 3"},
                {"id": "section_02", "title": "The Gradient", "duration": 132.0, "n_cues": 3, "position": "2 of 3"},
                {"id": "section_03", "title": "Convergence", "duration": 61.1, "n_cues": 1, "position": "3 of 3"},
            ],
            "pacing_notes": "Section 'The Gradient' is longest at 2m12s",
        }

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_overview_injected_into_user_message(self, mock_paths, mock_chat):
        """When overview is passed, user message must contain 'section N of M'."""
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        mock_chat.return_value = textwrap.dedent("""
            from manimlib import *

            class Section02Scene(Scene):
                def construct(self):
                    self.wait(5.0)
        """).strip()

        from manimgen.generator.scene_generator import generate_scenes
        generate_scenes(
            self._make_section(),
            cue_durations=[132.0],
            overview=self._make_overview(),
        )

        assert mock_chat.called
        call_kwargs = mock_chat.call_args
        user_msg = call_kwargs[1].get("user") or call_kwargs[0][1]
        assert "2 of 3" in user_msg, f"Expected '2 of 3' in user message. Got:\n{user_msg[:500]}"

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_total_duration_in_user_message(self, mock_paths, mock_chat):
        """Overview injection includes total video duration."""
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        mock_chat.return_value = "from manimlib import *\n\nclass Section02Scene(Scene):\n    def construct(self):\n        self.wait(5.0)\n"

        from manimgen.generator.scene_generator import generate_scenes
        generate_scenes(
            self._make_section(),
            cue_durations=[132.0],
            overview=self._make_overview(),
        )

        user_msg = mock_chat.call_args[1].get("user") or mock_chat.call_args[0][1]
        assert "287" in user_msg, f"Expected total duration '287' in user message."

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_no_overview_still_works(self, mock_paths, mock_chat):
        """`generate_scenes(section)` with no overview arg works as before."""
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        mock_chat.return_value = "from manimlib import *\n\nclass Section02Scene(Scene):\n    def construct(self):\n        self.wait(5.0)\n"

        from manimgen.generator.scene_generator import generate_scenes
        code, class_name, scene_path = generate_scenes(
            self._make_section(),
            cue_durations=[132.0],
            # No overview arg
        )
        assert "from manimlib import *" in code

    @patch("manimgen.generator.scene_generator.chat")
    @patch("manimgen.generator.scene_generator.paths")
    def test_overview_none_still_works(self, mock_paths, mock_chat):
        """`generate_scenes(section, overview=None)` does not crash."""
        mock_paths.scenes_dir.return_value = "/tmp/manimgen_test_scenes"
        os.makedirs("/tmp/manimgen_test_scenes", exist_ok=True)
        mock_chat.return_value = "from manimlib import *\n\nclass Section02Scene(Scene):\n    def construct(self):\n        self.wait(5.0)\n"

        from manimgen.generator.scene_generator import generate_scenes
        code, class_name, scene_path = generate_scenes(
            self._make_section(),
            cue_durations=[132.0],
            overview=None,
        )
        assert "from manimlib import *" in code
```

- [ ] **Step 2: Run the tests to confirm they fail**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/test_overview_injection.py -v 2>&1 | head -40
```

Expected: `TypeError` on `generate_scenes()` call — `overview` param doesn't exist yet.

---

## Task 6: Add `overview` parameter to `generate_scenes()` and `_build_user_message()`

**Files:**
- Modify: `manimgen/manimgen/manimgen/generator/scene_generator.py`

- [ ] **Step 1: Update `_build_user_message()` to accept and inject `overview`**

Find the current `_build_user_message` signature:
```python
def _build_user_message(section: dict, cue_durations: list[float]) -> str:
```

Replace with:
```python
def _build_user_message(section: dict, cue_durations: list[float], overview: dict | None = None) -> str:
```

Then find the `lines` list construction at the top of the function body — after `lines = [...]` and before `for i, dur in enumerate(...)`, insert the overview block. Specifically, after the initial `lines` definition ending with `""`, add:

```python
    if overview:
        section_id = section.get("id", "")
        section_entry = next(
            (s for s in overview.get("sections", []) if s["id"] == section_id),
            None,
        )
        if section_entry:
            total_dur = overview.get("total_duration", 0)
            pos = section_entry.get("position", "?")
            sec_dur = section_entry.get("duration", 0)
            n_cues = section_entry.get("n_cues", 0)
            pacing = overview.get("pacing_notes", "")
            context_lines = [
                "## Video context",
                f"This is section {pos} in a {total_dur:.0f}s video.",
                f"Section duration: {sec_dur:.0f}s across {n_cues} cues.",
            ]
            if pacing:
                context_lines.append(pacing)
            context_lines.append("")
            lines = context_lines + lines
```

- [ ] **Step 2: Update `generate_scenes()` signature to accept `overview`**

Find:
```python
def generate_scenes(
    section: dict,
    cue_durations: list[float] | None = None,
    duration_seconds: float | None = None,
) -> tuple[str, str, str]:
```

Replace with:
```python
def generate_scenes(
    section: dict,
    cue_durations: list[float] | None = None,
    duration_seconds: float | None = None,
    overview: dict | None = None,
) -> tuple[str, str, str]:
```

Then find the `_build_user_message` call inside `generate_scenes`:
```python
    user_message = _build_user_message(section, durations)
```

Replace with:
```python
    user_message = _build_user_message(section, durations, overview=overview)
```

- [ ] **Step 3: Run the overview injection tests**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/test_overview_injection.py -v
```

Expected: All 4 tests PASS.

- [ ] **Step 4: Run the full test suite**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

Expected: All tests pass (463+ passing, 0 failed).

- [ ] **Step 5: Commit scene_generator.py changes**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
git add manimgen/generator/scene_generator.py tests/test_overview_injection.py
git commit -m "feat(scene_generator): inject global video context into Director user message"
```

---

## Task 7: Final verification

**Files:** None (verification only)

- [ ] **Step 1: Run the complete test suite one final time**

```bash
cd /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen
python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
```

Expected output: `X passed` with X ≥ 463. Zero failures, zero errors.

- [ ] **Step 2: Verify both commits are on main**

```bash
git log --oneline -5
```

Expected: Two new commits at the top — one for cli.py, one for scene_generator.py.

- [ ] **Step 3: Run requesting-code-review skill**

Use the `superpowers:requesting-code-review` skill.

---

## Self-Review Against Spec

**Spec requirement → Task coverage:**

| Requirement | Task |
|---|---|
| Phase 1: TTS all sections upfront | Task 4 (`main()` Phase 1 loop) |
| Phase 1: Build global overview | Task 2 (`_build_overview`) |
| `_build_overview` returns correct shape | Task 2 |
| `_run_section` no longer calls `_run_tts_for_section` | Task 3 |
| `generate_scenes` accepts `overview` param | Task 6 |
| Overview injected into Director user message | Task 6 |
| `section_audio=None` path (TTS off) still works | Task 3 tests |
| Test: `test_all_tts_runs_before_any_codegen` | Task 1 |
| Test: `test_build_overview_total_duration` | Task 1 |
| Test: `test_build_overview_section_positions` | Task 1 |
| Test: `test_run_section_uses_precomputed_audio` | Task 1 |
| Test: `test_tts_off_still_works` | Task 1 |
| Test: `test_overview_injected_into_prompt` | Task 5 |
| Test: `test_no_overview_still_works` | Task 5 |
| Commit after cli.py | Task 4 step 4 |
| Commit after scene_generator.py | Task 6 step 5 |
| No new dependencies | Throughout — zero new imports |
| Don't touch codeguard/retry/assembler/timing_verifier | Not referenced anywhere in plan |
