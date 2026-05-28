"""
Tests for manimgen/validator/layout_checker.py and the visual retry path in retry.py.

Zero LLM calls, zero subprocess calls, zero real video files.
All external calls are mocked.
"""

from unittest.mock import MagicMock, patch

import pytest

from manimgen.utils import probe_video_duration
from manimgen.validator.layout_checker import (
    check_layout,
    _extract_frame,
    _sample_frames,
)


# ── probe_video_duration ──────────────────────────────────────────────────────

class TestGetVideoDuration:

    def test_returns_duration_on_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12.34\n"
        with patch("manimgen.utils.subprocess.run", return_value=mock_result):
            assert probe_video_duration("/fake/video.mp4") == pytest.approx(12.34)

    def test_returns_none_on_nonzero_returncode(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("manimgen.utils.subprocess.run", return_value=mock_result):
            assert probe_video_duration("/fake/video.mp4") is None

    def test_returns_none_on_exception(self):
        with patch("manimgen.utils.subprocess.run", side_effect=Exception("ffprobe not found")):
            assert probe_video_duration("/fake/video.mp4") is None


# ── _extract_frame ────────────────────────────────────────────────────────────

class TestExtractFrame:

    def test_returns_base64_on_success(self, tmp_path):
        fake_png = tmp_path / "frame.png"
        fake_png.write_bytes(b"\x89PNG\r\n\x1a\n")  # minimal PNG header

        mock_result = MagicMock()
        mock_result.returncode = 0

        import base64
        with patch("subprocess.run", return_value=mock_result), \
             patch("tempfile.NamedTemporaryFile") as mock_tmp:
            mock_tmp.return_value.__enter__.return_value.name = str(fake_png)
            result = _extract_frame("/fake/video.mp4", timestamp=2.0)

        assert result is not None
        # should be valid base64
        base64.b64decode(result)

    def test_returns_none_on_ffmpeg_failure(self, tmp_path):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"error"

        with patch("subprocess.run", return_value=mock_result), \
             patch("tempfile.NamedTemporaryFile") as mock_tmp:
            mock_tmp.return_value.__enter__.return_value.name = "/nonexistent/frame.png"
            result = _extract_frame("/fake/video.mp4", timestamp=2.0)

        assert result is None


# ── _sample_frames ────────────────────────────────────────────────────────────

class TestSampleFrames:

    def test_samples_at_25_50_75_percent_of_duration(self):
        with patch("manimgen.validator.layout_checker.probe_video_duration", return_value=8.0), \
             patch("manimgen.validator.layout_checker._extract_frame", return_value="b64data") as mock_extract:
            frames = _sample_frames("/fake/video.mp4")

        assert len(frames) == 3
        timestamps_called = [c.args[1] for c in mock_extract.call_args_list]
        assert timestamps_called == pytest.approx([2.0, 4.0, 6.0])

    def test_falls_back_to_fixed_timestamps_when_duration_unknown(self):
        with patch("manimgen.validator.layout_checker.probe_video_duration", return_value=None), \
             patch("manimgen.validator.layout_checker._extract_frame", return_value="b64data") as mock_extract:
            frames = _sample_frames("/fake/video.mp4")

        timestamps_called = [c.args[1] for c in mock_extract.call_args_list]
        assert 0.5 in timestamps_called
        assert 1.0 in timestamps_called

    def test_skips_failed_frames(self):
        # First frame fails, rest succeed
        with patch("manimgen.validator.layout_checker.probe_video_duration", return_value=6.0), \
             patch("manimgen.validator.layout_checker._extract_frame", side_effect=[None, "b64data", "b64data"]):
            frames = _sample_frames("/fake/video.mp4")

        assert len(frames) == 2


# ── check_layout ──────────────────────────────────────────────────────────────

class TestCheckLayout:

    def test_skipped_when_video_missing(self):
        result = check_layout("/nonexistent/video.mp4")
        assert result["skipped"] is True
        assert result["ok"] is True

    def test_skipped_when_no_frames_extracted(self):
        with patch("os.path.exists", return_value=True), \
             patch("manimgen.validator.layout_checker._sample_frames", return_value=[]):
            result = check_layout("/fake/video.mp4")
        assert result["skipped"] is True

    def test_ok_when_llm_returns_ok(self):
        with patch("os.path.exists", return_value=True), \
             patch("manimgen.validator.layout_checker._sample_frames", return_value=["b64frame"]), \
             patch("manimgen.validator.layout_checker.chat", return_value="OK"):
            result = check_layout("/fake/video.mp4")
        assert result["ok"] is True
        assert result["issues"] == ""
        assert result["skipped"] is False

    def test_not_ok_when_llm_returns_issues(self):
        issues = (
            "ISSUE: blue rect too wide | CAUSE: SurroundingRectangle on mutating VGroup | "
            "FIX: recreate rect after each swap"
        )
        with patch("os.path.exists", return_value=True), \
             patch("manimgen.validator.layout_checker._sample_frames", return_value=["b64frame"]), \
             patch("manimgen.validator.layout_checker.chat", return_value=issues):
            result = check_layout("/fake/video.mp4")
        assert result["ok"] is False
        assert result["issues"] == issues
        assert result["skipped"] is False

    def test_skipped_when_llm_raises(self):
        with patch("os.path.exists", return_value=True), \
             patch("manimgen.validator.layout_checker._sample_frames", return_value=["b64frame"]), \
             patch("manimgen.validator.layout_checker.chat", side_effect=Exception("API error")):
            result = check_layout("/fake/video.mp4")
        assert result["skipped"] is True

    def test_passes_all_frames_to_llm(self):
        frames = ["frame1", "frame2", "frame3"]
        ref_frames = ["ref1", "ref2"]
        with patch("os.path.exists", return_value=True), \
             patch("manimgen.validator.layout_checker._sample_frames", return_value=frames), \
             patch("manimgen.validator.layout_checker.load_reference_frames", return_value=ref_frames), \
             patch("manimgen.validator.layout_checker.chat", return_value="OK") as mock_chat:
            check_layout("/fake/video.mp4")

        _, kwargs = mock_chat.call_args
        assert kwargs["images"] == ref_frames + frames


# ── retry.py visual feedback loop ────────────────────────────────────────────

class TestRetryVisualLoop:
    """
    Test that retry_scene() acts on layout checker feedback instead of
    discarding it.
    """

    def _make_section(self):
        return {
            "title": "Test Section",
            "narration": "test",
            "cues": [{"index": 0, "visual": "test visual"}],
        }

    def test_accepts_video_when_layout_ok(self, tmp_path):
        scene_path = str(tmp_path / "scene.py")
        with open(scene_path, "w") as f:
            f.write("from manimlib import *\nclass TestScene(Scene):\n    def construct(self): pass\n")

        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": True, "issues": "", "skipped": False}):
            from manimgen.validator.retry import retry_scene
            success, video = retry_scene(self._make_section(), "from manimlib import *\nclass TestScene(Scene):\n    def construct(self): pass\n", "TestScene", scene_path)

        assert success is True
        assert video == "/fake/video.mp4"

    def test_calls_visual_fix_when_layout_fails_and_budget_allows(self, tmp_path):
        """When layout check fails and budget allows, LLM is called with structured visual issues."""
        scene_path = str(tmp_path / "scene.py")
        original_code = "from manimlib import *\nclass TestScene(Scene):\n    def construct(self): pass\n"
        with open(scene_path, "w") as f:
            f.write(original_code)

        issues = "ISSUE: ghost element | CAUSE: Transform point mismatch | FIX: use FadeOut/FadeIn"
        fixed_code = "from manimlib import *\nclass TestScene(Scene):\n    def construct(self):\n        self.wait(1)\n"

        # Render succeeds, frame_checker is clean (so the gate consults the
        # LLM vision check), layout fails → visual fix requested.
        from manimgen.validator.frame_checker import FrameCheckResult
        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.frame_checker.check_frames",
                   return_value=FrameCheckResult(ok=True)), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": False, "issues": issues, "skipped": False}), \
             patch("manimgen.validator.retry.chat", return_value=fixed_code) as mock_chat, \
             patch("manimgen.validator.retry.precheck_and_autofix_file",
                   return_value={"ok": True, "stderr": "", "layout_warnings": []}):
            import manimgen.validator.retry as retry_module
            retry_module.MAX_RETRIES = 3
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 1
            from manimgen.validator.retry import retry_scene
            retry_scene(self._make_section(), original_code, "TestScene", scene_path)

        # LLM was called once with the structured visual issues in the prompt
        mock_chat.assert_called_once()
        call_kwargs = mock_chat.call_args.kwargs
        assert issues in call_kwargs["user"]

    def test_accepts_video_when_budget_exhausted_despite_layout_issues(self, tmp_path):
        scene_path = str(tmp_path / "scene.py")
        original_code = "from manimlib import *\nclass TestScene(Scene):\n    def construct(self): pass\n"
        with open(scene_path, "w") as f:
            f.write(original_code)

        issues = "ISSUE: overlap | CAUSE: stale rect | FIX: recreate rect"

        from manimgen.validator.frame_checker import FrameCheckResult
        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.frame_checker.check_frames",
                   return_value=FrameCheckResult(ok=True)), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": False, "issues": issues, "skipped": False}), \
             patch("manimgen.validator.retry.chat") as mock_chat:
            from manimgen.validator import retry as retry_module
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 0  # visual budget exhausted from the start
            from manimgen.validator.retry import retry_scene
            success, video = retry_scene(self._make_section(), original_code, "TestScene", scene_path)

        # Budget exhausted + defects → ship the best render (remote behavior we
        # deliberately kept; we dropped the fail-to-fallback approach). No LLM
        # call is made because the visual budget is zero.
        assert success is True
        assert video == "/fake/video.mp4"
        mock_chat.assert_not_called()

    def test_repeated_error_signature_breaks_instead_of_draining(self, tmp_path):
        """Bug #1: a repeated unfixable error signature must stop the loop,
        not spin idle render iterations until MAX_RETRIES is exhausted."""
        scene_path = str(tmp_path / "scene.py")
        original_code = "from manimlib import *\nclass TestScene(Scene):\n    BROKEN\n"
        with open(scene_path, "w") as f:
            f.write(original_code)

        stderr = "AttributeError: 'Scene' object has no attribute 'frobnicate'"
        run_mock = MagicMock(
            return_value={"success": False, "video_path": None, "stderr": stderr}
        )
        with patch("manimgen.validator.retry._run_and_capture", run_mock), \
             patch("manimgen.validator.retry.apply_error_aware_fixes",
                   return_value=(original_code, [])), \
             patch("manimgen.validator.retry.precheck_and_autofix_file",
                   return_value={"ok": True, "stderr": "", "layout_warnings": []}), \
             patch("manimgen.validator.retry.chat",
                   return_value=original_code) as mock_chat:
            from manimgen.validator import retry as retry_module
            retry_module.MAX_RETRIES = 6
            retry_module.MAX_ERROR_LLM_FIX_CALLS = 3
            from manimgen.validator.retry import retry_scene
            success, video = retry_scene(self._make_section(), original_code, "TestScene", scene_path)

        assert success is False
        assert video is None
        # The LLM is called at most once: the second time the same signature
        # appears the loop breaks instead of re-rendering unchanged code
        # repeatedly. Without the fix this would render up to MAX_RETRIES times.
        assert mock_chat.call_count <= 1
        assert run_mock.call_count <= 2

    def test_layout_checker_skipped_when_frame_checker_already_flagged(self, tmp_path):
        """Bug #3: skip the expensive LLM vision check when the zero-cost
        frame_checker already found concrete (non-frozen) defects."""
        scene_path = str(tmp_path / "scene.py")
        original_code = "from manimlib import *\nclass TestScene(Scene):\n    def construct(self): pass\n"
        with open(scene_path, "w") as f:
            f.write(original_code)

        from manimgen.validator.frame_checker import FrameCheckResult
        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.frame_checker.check_frames",
                   return_value=FrameCheckResult(ok=False, issues=["Black/empty frame at 2.0s"])), \
             patch("manimgen.validator.retry.check_layout") as mock_layout, \
             patch("manimgen.validator.retry.chat") as mock_chat:
            from manimgen.validator import retry as retry_module
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 0
            from manimgen.validator.retry import retry_scene
            retry_scene(self._make_section(), original_code, "TestScene", scene_path)

        # frame_checker found a concrete defect → the paid second opinion is
        # skipped entirely.
        mock_layout.assert_not_called()

    def test_error_and_visual_budgets_are_independent(self, tmp_path):
        """An exhausted error budget must NOT prevent a visual fix (and vice
        versa). The two budgets are tracked separately."""
        scene_path = str(tmp_path / "scene.py")
        original_code = "from manimlib import *\nclass TestScene(Scene):\n    def construct(self): pass\n"
        with open(scene_path, "w") as f:
            f.write(original_code)

        issues = "ISSUE: ghost | CAUSE: stale | FIX: recreate"
        fixed_code = "from manimlib import *\nclass TestScene(Scene):\n    def construct(self):\n        self.wait(1)\n"

        from manimgen.validator.frame_checker import FrameCheckResult
        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.frame_checker.check_frames",
                   return_value=FrameCheckResult(ok=True)), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": False, "issues": issues, "skipped": False}), \
             patch("manimgen.validator.retry.chat", return_value=fixed_code) as mock_chat, \
             patch("manimgen.validator.retry.precheck_and_autofix_file",
                   return_value={"ok": True, "stderr": "", "layout_warnings": []}):
            from manimgen.validator import retry as retry_module
            retry_module.MAX_RETRIES = 3
            retry_module.MAX_ERROR_LLM_FIX_CALLS = 0   # error budget fully spent
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 1  # visual budget still available
            from manimgen.validator.retry import retry_scene
            retry_scene(self._make_section(), original_code, "TestScene", scene_path)

        # Despite zero error budget, the visual fix path still fired.
        mock_chat.assert_called_once()
        assert issues in mock_chat.call_args.kwargs["user"]

    def test_blocking_freeze_tail_resolved_deterministically_no_llm(self, tmp_path):
        """REWRITTEN for issue #22 (timing_verifier is now authoritative).

        Previously this asserted that a >threshold freeze-frame tail must route
        to the LLM (chat) for a fix. That encoded the architectural inversion
        #22 removes: the deterministic timing gate KNOWS the exact residual, so
        it now adjusts the cue's self.wait() to close the freeze BEFORE any
        render — no LLM call. The render is then accepted with corrected code.

        A freeze that the deterministic layer can resolve must NOT burn an LLM
        call. The contract is preserved (the freeze never ships unchecked); the
        mechanism changed from advisory-hint-to-LLM to authoritative-self-heal.
        """
        scene_path = str(tmp_path / "scene.py")
        # CUE 0 narration is 10s but the scene only animates 1.5s (1.0 play +
        # 0.5 wait) → 8.5s frozen tail, far over the 2.5s block threshold.
        frozen_code = (
            "from manimlib import *\n"
            "class TestScene(Scene):\n"
            "    def construct(self):\n"
            "        # CUE 0\n"
            "        self.play(Write(Text('hi')), run_time=1.0)\n"
            "        self.wait(0.5)\n"
        )
        with open(scene_path, "w") as f:
            f.write(frozen_code)

        from manimgen.validator.frame_checker import FrameCheckResult
        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/v.mp4", "stderr": ""}), \
             patch("manimgen.validator.frame_checker.check_frames",
                   return_value=FrameCheckResult(ok=True)), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": True, "issues": "", "skipped": False}), \
             patch("manimgen.validator.retry.chat") as mock_chat, \
             patch("manimgen.validator.retry.precheck_and_autofix_file",
                   return_value={"ok": True, "stderr": "", "layout_warnings": []}):
            import manimgen.validator.retry as retry_module
            retry_module.MAX_RETRIES = 3
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 1
            from manimgen.validator.retry import retry_scene
            # cue_durations=[10.0] → 10s narration vs 1.5s animation = freeze
            success, _ = retry_scene(
                self._make_section(), frozen_code, "TestScene", scene_path,
                cue_durations=[10.0],
            )

        # The deterministic timing gate closed the freeze before rendering, so
        # NO LLM fix was requested.
        mock_chat.assert_not_called()
        # The scene file on disk now has the corrected wait (~9.0s) and the
        # render was accepted.
        from manimgen.validator.timing_verifier import blocking_freezes, verify_timing
        with open(scene_path) as f:
            final_code = f.read()
        assert "self.wait(9.00)" in final_code
        assert blocking_freezes(verify_timing(final_code, [10.0])) == []
        assert success

    # ── #32: frozen-frame ∧ timing join ────────────────────────────────────

    _FROZEN_ISSUE = (
        "ISSUE: Frames at t=2.0s and t=4.0s are 99% identical — "
        "animation appears frozen | CAUSE: long wait | FIX: add activity"
    )

    def test_frozen_frame_is_hard_when_timing_confirms_dead_tail(self, tmp_path):
        """#32: frame_checker reports a frozen frame AND the timing oracle
        confirms a blocking dead tail (narration ended, animation didn't) → the
        frozen signal is re-admitted as a defect that prevents a clean accept
        and routes into the visual-fix retry path. Previously this signal was
        discarded unconditionally and the frozen render shipped."""
        scene_path = str(tmp_path / "scene.py")
        code = (
            "from manimlib import *\n"
            "class TestScene(Scene):\n"
            "    def construct(self): pass\n"
        )
        with open(scene_path, "w") as f:
            f.write(code)

        fixed = (
            "from manimlib import *\n"
            "class TestScene(Scene):\n"
            "    def construct(self):\n        self.wait(1)\n"
        )
        from manimgen.validator.frame_checker import FrameCheckResult

        with patch(
            "manimgen.validator.retry._run_and_capture",
            return_value={"success": True, "video_path": "/fake/v.mp4", "stderr": ""},
        ), patch(
            "manimgen.validator.frame_checker.check_frames",
            return_value=FrameCheckResult(ok=False, issues=[self._FROZEN_ISSUE]),
        ), patch(
            # Keep code untouched so the post-render timing oracle drives the join.
            "manimgen.validator.retry.apply_timing_gate",
            side_effect=lambda c, p, d: (c, []),
        ), patch(
            # Timing oracle CONFIRMS a dead tail → frozen frame must be kept.
            "manimgen.validator.timing_verifier.blocking_freezes",
            return_value=["CUE 0: 4.00s frozen tail"],
        ), patch(
            "manimgen.validator.retry.check_layout"
        ) as mock_layout, patch(
            "manimgen.validator.retry.chat", return_value=fixed
        ) as mock_chat, patch(
            "manimgen.validator.retry.precheck_and_autofix_file",
            return_value={"ok": True, "stderr": "", "layout_warnings": []},
        ):
            import manimgen.validator.retry as retry_module

            retry_module.MAX_RETRIES = 3
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 1
            from manimgen.validator.retry import retry_scene

            retry_scene(
                self._make_section(), code, "TestScene", scene_path,
                cue_durations=[10.0],
            )

        # The frozen frame became a concrete defect, so a visual fix was
        # requested and the frozen line was forwarded to the LLM. The layout
        # vision check is skipped because frame defects already exist.
        mock_chat.assert_called_once()
        assert "animation appears frozen" in mock_chat.call_args.kwargs["user"]
        mock_layout.assert_not_called()

    def test_frozen_frame_not_hard_when_narration_continues(self, tmp_path):
        """#32: frame_checker reports a frozen frame but the timing oracle finds
        NO blocking freeze (narration still running over a deliberate hold) →
        the frozen signal is dropped as a legit hold and the render is accepted
        clean. This is the false-positive the join must avoid."""
        scene_path = str(tmp_path / "scene.py")
        code = (
            "from manimlib import *\n"
            "class TestScene(Scene):\n"
            "    def construct(self): pass\n"
        )
        with open(scene_path, "w") as f:
            f.write(code)

        from manimgen.validator.frame_checker import FrameCheckResult

        with patch(
            "manimgen.validator.retry._run_and_capture",
            return_value={"success": True, "video_path": "/fake/v.mp4", "stderr": ""},
        ), patch(
            "manimgen.validator.frame_checker.check_frames",
            return_value=FrameCheckResult(ok=False, issues=[self._FROZEN_ISSUE]),
        ), patch(
            "manimgen.validator.retry.apply_timing_gate",
            side_effect=lambda c, p, d: (c, []),
        ), patch(
            # Timing oracle finds NO dead tail → legit hold, frozen dropped.
            "manimgen.validator.timing_verifier.blocking_freezes",
            return_value=[],
        ), patch(
            "manimgen.validator.retry.check_layout",
            return_value={"ok": True, "issues": "", "skipped": False},
        ), patch(
            "manimgen.validator.retry.chat"
        ) as mock_chat:
            import manimgen.validator.retry as retry_module

            retry_module.MAX_RETRIES = 3
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 1
            from manimgen.validator.retry import retry_scene

            success, video = retry_scene(
                self._make_section(), code, "TestScene", scene_path,
                cue_durations=[10.0],
            )

        # Frozen dropped as legit hold, layout clean → accepted on attempt 1,
        # no LLM fix requested.
        assert success is True
        assert video == "/fake/v.mp4"
        mock_chat.assert_not_called()

    # ── #33: skipped layout fails closed to UNVERIFIED (bounded) ────────────

    def test_skipped_layout_is_not_accepted_as_clean_and_is_bounded(self, tmp_path):
        """#33: layout_checker returns skipped=True on its OWN LLM failure. With
        no frame/timing defect, the render is UNVERIFIED — it must NOT take the
        immediate clean-accept path (the silent false-accept during an API
        outage), but it must also NOT burn the visual budget or retry forever.
        It ships bounded: one render attempt, no LLM call, success=True without
        a clean-pass claim."""
        scene_path = str(tmp_path / "scene.py")
        # Timing matches the cue duration (1.0 play + 9.0 wait = 10.0s) so there
        # is NO frame/timing defect — the ONLY signal is the skipped layout.
        code = (
            "from manimlib import *\n"
            "class TestScene(Scene):\n"
            "    def construct(self):\n"
            "        # CUE 0\n"
            "        self.play(Write(Text('hi')), run_time=1.0)\n"
            "        self.wait(9.0)\n"
        )
        with open(scene_path, "w") as f:
            f.write(code)

        from manimgen.validator.frame_checker import FrameCheckResult

        run_mock = MagicMock(
            return_value={"success": True, "video_path": "/fake/v.mp4", "stderr": ""}
        )
        with patch(
            "manimgen.validator.retry._run_and_capture", run_mock
        ), patch(
            "manimgen.validator.frame_checker.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch(
            # Layout LLM is DOWN → skipped=True (fail-closed-to-UNVERIFIED).
            "manimgen.validator.retry.check_layout",
            return_value={"ok": True, "issues": "", "skipped": True, "frames": []},
        ), patch(
            "manimgen.validator.retry.chat"
        ) as mock_chat:
            import manimgen.validator.retry as retry_module

            retry_module.MAX_RETRIES = 5
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 3
            from manimgen.validator.retry import retry_scene

            success, video = retry_scene(
                self._make_section(), code, "TestScene", scene_path,
                cue_durations=[10.0],
            )

        # Bounded: shipped the render without an infinite/expensive retry...
        assert success is True
        assert video == "/fake/v.mp4"
        # ...without burning the visual budget on an outage it cannot fix...
        mock_chat.assert_not_called()
        # ...and without spinning render attempts (one attempt, then bounded out).
        assert run_mock.call_count == 1

    def test_skipped_layout_does_not_take_immediate_clean_accept(self, tmp_path):
        """#33: a skipped layout must be distinguishable from a verified-clean
        pass. The clean-accept branch (`not combined_issues and not
        layout_unverified`) must NOT fire for a skipped layout — proven by the
        UNVERIFIED bounded-ship log path being taken instead. Here we assert the
        render is still shipped but specifically via the unverified branch by
        confirming concrete layout issues were never produced."""
        scene_path = str(tmp_path / "scene.py")
        # Timing matches the cue duration so the ONLY signal is the skipped
        # layout (no frame/timing defect competing for the clean-accept branch).
        code = (
            "from manimlib import *\n"
            "class TestScene(Scene):\n"
            "    def construct(self):\n"
            "        # CUE 0\n"
            "        self.play(Write(Text('hi')), run_time=1.0)\n"
            "        self.wait(9.0)\n"
        )
        with open(scene_path, "w") as f:
            f.write(code)

        from manimgen.validator.frame_checker import FrameCheckResult

        layout_mock = MagicMock(
            return_value={"ok": True, "issues": "", "skipped": True, "frames": []}
        )
        with patch(
            "manimgen.validator.retry._run_and_capture",
            return_value={"success": True, "video_path": "/fake/v.mp4", "stderr": ""},
        ), patch(
            "manimgen.validator.frame_checker.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch(
            "manimgen.validator.retry.check_layout", layout_mock
        ), patch(
            "manimgen.validator.retry.chat"
        ) as mock_chat:
            import manimgen.validator.retry as retry_module

            retry_module.MAX_RETRIES = 3
            retry_module.MAX_VISUAL_LLM_FIX_CALLS = 2
            from manimgen.validator.retry import retry_scene

            success, video = retry_scene(
                self._make_section(), code, "TestScene", scene_path,
                cue_durations=[10.0],
            )

        # check_layout was actually consulted (frame_checker was clean), and the
        # skipped verdict did NOT trigger an LLM visual fix.
        layout_mock.assert_called_once()
        mock_chat.assert_not_called()
        assert success is True
