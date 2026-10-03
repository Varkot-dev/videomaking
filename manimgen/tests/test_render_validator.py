"""Tests for the unified render_validator quality gate.

Covers the #32 frozen-frame ∧ timing join and the #33 fail-closed-to-UNVERIFIED
layout behavior as exercised through the cli first-pass path.

Zero LLM calls, zero subprocess calls, zero real video files — every external
dependency is mocked.
"""

from unittest.mock import patch

import pytest

from manimgen.validator.frame_checker import FrameCheckResult
from manimgen.validator.render_validator import validate_render

# A scene whose timing is statically resolvable so verify_timing produces a
# real (non-UNKNOWN) result the join can reason about.
_CODE = (
    "from manimlib import *\n"
    "class TestScene(Scene):\n"
    "    def construct(self):\n"
    "        # CUE 0\n"
    "        self.play(Write(Text('hi')), run_time=1.0)\n"
    "        self.wait(0.5)\n"
)

_FROZEN_ISSUE = (
    "ISSUE: Frames at t=2.0s and t=4.0s are 99% identical — "
    "animation appears frozen | CAUSE: long wait | FIX: add activity"
)
_BLACK_ISSUE = "ISSUE: Black/empty frame at t=2.0s | CAUSE: faded | FIX: keep content"


def _video(tmp_path):
    """Create a real (empty) file so the os.path.exists guard passes."""
    v = tmp_path / "render.mp4"
    v.write_bytes(b"\x00")
    return str(v)


class TestMissingVideo:
    def test_missing_video_is_none_severity(self):
        result = validate_render("/nonexistent.mp4", _CODE, "/s.py", [10.0])
        assert result.ok is True
        assert result.severity == "none"
        assert result.issues == []


class TestFrozenTimingJoin:
    """#32 at the cli path."""

    def test_frozen_frame_hard_when_timing_confirms_dead_tail(self, tmp_path):
        video = _video(tmp_path)
        # cue 0 narration is 10s but the scene animates only 1.5s → dead tail.
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=False, issues=[_FROZEN_ISSUE]),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": True, "issues": "", "skipped": False},
        ):
            result = validate_render(video, _CODE, "/s.py", [10.0])

        # timing confirms the freeze → frozen frame admitted as a HARD failure.
        assert result.ok is False
        assert result.severity == "hard"
        assert any("animation appears frozen" in i for i in result.issues)

    def test_frozen_frame_not_hard_when_narration_matches(self, tmp_path):
        video = _video(tmp_path)
        # cue 0 narration is 1.5s and the scene animates 1.5s → no dead tail.
        # The frozen frame is a legit hold and must be dropped (no false hard).
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=False, issues=[_FROZEN_ISSUE]),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": True, "issues": "", "skipped": False},
        ):
            result = validate_render(video, _CODE, "/s.py", [1.5])

        assert result.ok is True
        assert result.severity == "none"
        assert result.issues == []

    def test_frozen_dropped_when_cue_durations_none(self, tmp_path):
        video = _video(tmp_path)
        # No cue_durations → timing cannot confirm → frozen dropped, layout
        # skipped entirely (TTS off).
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=False, issues=[_FROZEN_ISSUE]),
        ), patch(
            "manimgen.validator.render_validator.check_layout"
        ) as mock_layout:
            result = validate_render(video, _CODE, "/s.py", None)

        assert result.ok is True
        assert result.severity == "none"
        mock_layout.assert_not_called()

    def test_black_frame_always_hard_independent_of_timing(self, tmp_path):
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=False, issues=[_BLACK_ISSUE]),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": True, "issues": "", "skipped": False},
        ):
            # narration matches animation (no freeze) — black frame still hard.
            result = validate_render(video, _CODE, "/s.py", [1.5])

        assert result.ok is False
        assert result.severity == "hard"


class TestFirstPassLayoutDefaultOff:
    """R10 (owner decision, option B): the first-pass vision check is not run by
    default because its verdict was never enforced. It stays available behind
    MANIMGEN_FIRST_PASS_LAYOUT."""

    def test_default_makes_no_layout_call(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MANIMGEN_FIRST_PASS_LAYOUT", raising=False)
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch("manimgen.validator.render_validator.check_layout") as mock_layout:
            result = validate_render(video, _CODE, "/s.py", [1.5])

        mock_layout.assert_not_called()
        assert result.severity == "none"
        assert result.issues == []

    @pytest.mark.parametrize("value", ["0", "false", "off", "no", ""])
    def test_falsey_values_stay_off(self, tmp_path, monkeypatch, value):
        monkeypatch.setenv("MANIMGEN_FIRST_PASS_LAYOUT", value)
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch("manimgen.validator.render_validator.check_layout") as mock_layout:
            validate_render(video, _CODE, "/s.py", [1.5])
        mock_layout.assert_not_called()

    def test_opt_in_runs_layout(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MANIMGEN_FIRST_PASS_LAYOUT", "1")
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": False, "issues": "ISSUE: x | CAUSE: y | FIX: z", "skipped": False},
        ) as mock_layout:
            result = validate_render(video, _CODE, "/s.py", [1.5])

        mock_layout.assert_called_once()
        assert result.severity == "soft"

    def test_opt_in_still_skipped_without_tts(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MANIMGEN_FIRST_PASS_LAYOUT", "1")
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch("manimgen.validator.render_validator.check_layout") as mock_layout:
            validate_render(video, _CODE, "/s.py", None)
        mock_layout.assert_not_called()


@pytest.fixture
def _layout_on(monkeypatch):
    monkeypatch.setenv("MANIMGEN_FIRST_PASS_LAYOUT", "1")


@pytest.mark.usefixtures("_layout_on")
class TestSkippedLayoutUnverified:
    """#33 at the cli path: a skipped layout becomes a SOFT 'unverified' issue,
    never a silent verified-clean (none) pass (when the layout check is on)."""

    def test_skipped_layout_is_soft_not_none(self, tmp_path):
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": True, "issues": "", "skipped": True},
        ):
            result = validate_render(video, _CODE, "/s.py", [1.5])

        # ok stays True (no hard failure) but it is NOT a clean 'none' pass —
        # the unverified state is surfaced as a soft issue.
        assert result.ok is True
        assert result.severity == "soft"
        assert any("UNVERIFIED" in i for i in result.issues)

    def test_clean_layout_is_none(self, tmp_path):
        video = _video(tmp_path)
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": True, "issues": "", "skipped": False},
        ):
            result = validate_render(video, _CODE, "/s.py", [1.5])

        assert result.ok is True
        assert result.severity == "none"
        assert result.issues == []

    def test_real_layout_issues_are_soft(self, tmp_path):
        video = _video(tmp_path)
        issues = "ISSUE: overlap | CAUSE: stale rect | FIX: recreate"
        with patch(
            "manimgen.validator.render_validator.check_frames",
            return_value=FrameCheckResult(ok=True),
        ), patch(
            "manimgen.validator.render_validator.check_layout",
            return_value={"ok": False, "issues": issues, "skipped": False},
        ):
            result = validate_render(video, _CODE, "/s.py", [1.5])

        assert result.ok is True
        assert result.severity == "soft"
        assert any("overlap" in i for i in result.issues)
