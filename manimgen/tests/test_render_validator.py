"""Tests for render_validator.validate_render()."""
from dataclasses import dataclass
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Stubs
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
    p.write_bytes(b"fake")
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
    bad_frame = _FakeFrameResult(
        ok=False,
        issues=["ISSUE: Black/empty frame at t=1.0s | CAUSE: scene empty | FIX: add objects"],
    )
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
# validate_render — hard failure (frozen frames)
# ---------------------------------------------------------------------------

def test_validate_render_hard_frozen_frames(tmp_path):
    video = _make_video(tmp_path)
    bad_frame = _FakeFrameResult(
        ok=False,
        issues=["ISSUE: Frames at t=1.0s and t=2.0s are 99% identical — animation appears frozen | CAUSE: long wait | FIX: add animation"],
    )
    clean_layout = {"ok": True, "issues": "", "skipped": False}

    with patch("manimgen.validator.render_validator.check_frames", return_value=bad_frame), \
         patch("manimgen.validator.render_validator.check_layout", return_value=clean_layout):
        from manimgen.validator.render_validator import validate_render
        result = validate_render(video, "code", "/tmp/s.py", None)

    assert result.ok is False
    assert result.severity == "hard"


# ---------------------------------------------------------------------------
# validate_render — soft failure (layout issue only)
# ---------------------------------------------------------------------------

def test_validate_render_soft_layout(tmp_path):
    video = _make_video(tmp_path)
    clean_frame = _FakeFrameResult(ok=True, issues=[])
    layout_issue = {
        "ok": False,
        "issues": "ISSUE: Label overlap | CAUSE: two labels at same anchor | FIX: use VGroup.arrange()",
        "skipped": False,
        "frames": [],
    }

    with patch("manimgen.validator.render_validator.check_frames", return_value=clean_frame), \
         patch("manimgen.validator.render_validator.check_layout", return_value=layout_issue):
        from manimgen.validator.render_validator import validate_render
        result = validate_render(video, "from manimlib import *\n", "/tmp/scene.py", [2.5, 3.0])

    assert result.ok is True
    assert result.severity == "soft"
    assert any("Label overlap" in i for i in result.issues)


# ---------------------------------------------------------------------------
# validate_render — layout_checker skipped when cue_durations is None
# ---------------------------------------------------------------------------

def test_validate_render_skips_layout_without_tts(tmp_path):
    video = _make_video(tmp_path)
    clean_frame = _FakeFrameResult(ok=True, issues=[])

    with patch("manimgen.validator.render_validator.check_frames", return_value=clean_frame), \
         patch("manimgen.validator.render_validator.check_layout") as mock_layout:
        from manimgen.validator.render_validator import validate_render
        validate_render(video, "code", "/tmp/s.py", cue_durations=None)

    mock_layout.assert_not_called()


# ---------------------------------------------------------------------------
# validate_render — layout_checker called when cue_durations provided
# ---------------------------------------------------------------------------

def test_validate_render_calls_layout_with_tts(tmp_path):
    video = _make_video(tmp_path)
    clean_frame = _FakeFrameResult(ok=True, issues=[])
    clean_layout = {"ok": True, "issues": "", "skipped": False}

    with patch("manimgen.validator.render_validator.check_frames", return_value=clean_frame), \
         patch("manimgen.validator.render_validator.check_layout", return_value=clean_layout) as mock_layout:
        from manimgen.validator.render_validator import validate_render
        validate_render(video, "code", "/tmp/s.py", cue_durations=[2.0, 3.0])

    mock_layout.assert_called_once_with(video)


# ---------------------------------------------------------------------------
# validate_render — missing video returns clean (skip)
# ---------------------------------------------------------------------------

def test_validate_render_missing_video():
    from manimgen.validator.render_validator import validate_render
    result = validate_render("/nonexistent/path.mp4", "code", "/tmp/s.py", None)
    assert result.ok is True
    assert result.severity == "none"
