"""
Tests for manimgen/validator/layout_checker.py and the visual retry path in retry.py.

Zero LLM calls, zero subprocess calls, zero real video files.
All external calls are mocked.
"""

import os
from unittest.mock import MagicMock, patch, call

import pytest

from manimgen.validator.layout_checker import (
    check_layout,
    _get_video_duration,
    _extract_frame,
    _sample_frames,
)


# ── _get_video_duration ───────────────────────────────────────────────────────

class TestGetVideoDuration:

    def test_returns_duration_on_success(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "12.34\n"
        with patch("subprocess.run", return_value=mock_result):
            assert _get_video_duration("/fake/video.mp4") == pytest.approx(12.34)

    def test_returns_none_on_nonzero_returncode(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            assert _get_video_duration("/fake/video.mp4") is None

    def test_returns_none_on_exception(self):
        with patch("subprocess.run", side_effect=Exception("ffprobe not found")):
            assert _get_video_duration("/fake/video.mp4") is None


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
        with patch("manimgen.validator.layout_checker._get_video_duration", return_value=8.0), \
             patch("manimgen.validator.layout_checker._extract_frame", return_value="b64data") as mock_extract:
            frames = _sample_frames("/fake/video.mp4")

        assert len(frames) == 3
        timestamps_called = [c.args[1] for c in mock_extract.call_args_list]
        assert timestamps_called == pytest.approx([2.0, 4.0, 6.0])

    def test_falls_back_to_fixed_timestamps_when_duration_unknown(self):
        with patch("manimgen.validator.layout_checker._get_video_duration", return_value=None), \
             patch("manimgen.validator.layout_checker._extract_frame", return_value="b64data") as mock_extract:
            frames = _sample_frames("/fake/video.mp4")

        timestamps_called = [c.args[1] for c in mock_extract.call_args_list]
        assert 0.5 in timestamps_called
        assert 1.0 in timestamps_called

    def test_skips_failed_frames(self):
        # First frame fails, rest succeed
        with patch("manimgen.validator.layout_checker._get_video_duration", return_value=6.0), \
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
        with patch("os.path.exists", return_value=True), \
             patch("manimgen.validator.layout_checker._sample_frames", return_value=frames), \
             patch("manimgen.validator.layout_checker.chat", return_value="OK") as mock_chat:
            check_layout("/fake/video.mp4")

        _, kwargs = mock_chat.call_args
        assert kwargs["images"] == frames


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

        # Render succeeds but layout fails on first attempt — after visual fix, loop exits (budget gone)
        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": False, "issues": issues, "skipped": False}), \
             patch("manimgen.validator.retry.chat", return_value=fixed_code) as mock_chat, \
             patch("manimgen.validator.retry.precheck_and_autofix",
                   return_value={"ok": True, "stderr": "", "layout_warnings": []}):
            import manimgen.validator.retry as retry_module
            retry_module.MAX_RETRIES = 3
            retry_module.MAX_LLM_FIX_CALLS = 1
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

        with patch("manimgen.validator.retry._run_and_capture",
                   return_value={"success": True, "video_path": "/fake/video.mp4", "stderr": ""}), \
             patch("manimgen.validator.retry.check_layout",
                   return_value={"ok": False, "issues": issues, "skipped": False}), \
             patch("manimgen.validator.retry.chat") as mock_chat:
            from manimgen.validator import retry as retry_module
            retry_module.MAX_LLM_FIX_CALLS = 0  # budget exhausted from the start
            from manimgen.validator.retry import retry_scene
            success, video = retry_scene(self._make_section(), original_code, "TestScene", scene_path)

        assert success is True
        assert video == "/fake/video.mp4"
        mock_chat.assert_not_called()
