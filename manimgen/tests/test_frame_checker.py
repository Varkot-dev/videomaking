"""Tests for the deterministic frame checker (Tier 1 visual validation)."""
from __future__ import annotations

import time

import numpy as np
import pytest

# Frame checker functions are tested at the unit level — we create fake PIL
# images instead of rendering real video (that would require manimgl + ffmpeg).
try:
    from PIL import Image, ImageDraw
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from manimgen.validator import frame_checker
from manimgen.validator.frame_checker import (
    _changed_fraction,
    _small_array,
    FrameCheckResult,
    _check_black_frame,
    _check_edge_clipping,
    _check_frozen_frames,
    _BACKGROUND_COLOR,
)

def _text_supports_size() -> bool:
    """ImageDraw.text(font_size=) needs Pillow >= 10.1 with FreeType."""
    try:
        ImageDraw.Draw(Image.new("RGB", (10, 10))).text((0, 0), "x", font_size=12)
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _HAS_PIL, reason="PIL not installed")


# -----------------------------------------------------------------------
# Helpers — create synthetic test images
# -----------------------------------------------------------------------

def _solid_image(color: tuple[int, int, int], size: tuple[int, int] = (320, 180)) -> Image.Image:
    """Create a solid-colored image."""
    return Image.new("RGB", size, color)


def _background_with_content(size: tuple[int, int] = (320, 180)) -> Image.Image:
    """Create a dark background image with some bright content in the center."""
    img = Image.new("RGB", size, _BACKGROUND_COLOR)
    # Draw a bright rectangle in the center
    w, h = size
    for x in range(w // 4, 3 * w // 4):
        for y in range(h // 4, 3 * h // 4):
            img.putpixel((x, y), (200, 200, 100))  # yellowish content
    return img


def _content_at_edge(edge: str, size: tuple[int, int] = (320, 180)) -> Image.Image:
    """Create an image with bright content touching the specified edge."""
    img = Image.new("RGB", size, _BACKGROUND_COLOR)
    w, h = size
    if edge == "top":
        for x in range(w // 3, 2 * w // 3):
            for y in range(0, 8):
                img.putpixel((x, y), (255, 255, 0))
    elif edge == "bottom":
        for x in range(w // 3, 2 * w // 3):
            for y in range(h - 8, h):
                img.putpixel((x, y), (255, 255, 0))
    elif edge == "left":
        for x in range(0, 8):
            for y in range(h // 3, 2 * h // 3):
                img.putpixel((x, y), (255, 255, 0))
    elif edge == "right":
        for x in range(w - 8, w):
            for y in range(h // 3, 2 * h // 3):
                img.putpixel((x, y), (255, 255, 0))
    return img


# -----------------------------------------------------------------------
# Black frame detection
# -----------------------------------------------------------------------

class TestBlackFrame:
    def test_pure_black_detected(self):
        img = _solid_image((0, 0, 0))
        issue = _check_black_frame(img, 1.0)
        assert issue is not None
        assert "Black" in issue or "black" in issue.lower()

    def test_near_black_detected(self):
        img = _solid_image((5, 5, 5))
        issue = _check_black_frame(img, 1.0)
        assert issue is not None

    def test_flat_background_frame_is_flagged_as_empty(self):
        """R30: a flat #1C1C1C frame is an empty scene. The mean-brightness test
        alone could never fire on it (28 > threshold), so it shipped."""
        for size in ((320, 180), (1920, 1080)):
            issue = _check_black_frame(_solid_image(_BACKGROUND_COLOR, size), 1.0)
            assert issue is not None
            assert "Black/empty frame" in issue

    def test_background_with_compression_noise_still_empty(self):
        rng = np.random.default_rng(1)
        arr = np.full((180, 320, 3), _BACKGROUND_COLOR, dtype=np.int16)
        arr += rng.integers(-2, 3, arr.shape)
        img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
        assert _check_black_frame(img, 1.0) is not None

    def test_sparse_legitimate_frame_not_flagged(self):
        """A single short line of text on the background is real content."""
        img = Image.new("RGB", (1920, 1080), _BACKGROUND_COLOR)
        ImageDraw.Draw(img).text((200, 500), "Hello world", fill=(255, 255, 255), font_size=48) \
            if _text_supports_size() else ImageDraw.Draw(img).rectangle((200, 500, 700, 540), fill=(255, 255, 255))
        assert _check_black_frame(img, 1.0) is None

    def test_bright_image_not_flagged(self):
        img = _solid_image((200, 200, 200))
        issue = _check_black_frame(img, 1.0)
        assert issue is None

    def test_normal_scene_not_flagged(self):
        img = _background_with_content()
        issue = _check_black_frame(img, 1.0)
        assert issue is None


# -----------------------------------------------------------------------
# Edge clipping detection
# -----------------------------------------------------------------------

class TestEdgeClipping:
    def test_content_at_top_edge(self):
        img = _content_at_edge("top")
        issue = _check_edge_clipping(img, 1.0)
        assert issue is not None
        assert "top" in issue

    def test_content_at_bottom_edge(self):
        img = _content_at_edge("bottom")
        issue = _check_edge_clipping(img, 1.0)
        assert issue is not None
        assert "bottom" in issue

    def test_content_at_left_edge(self):
        img = _content_at_edge("left")
        issue = _check_edge_clipping(img, 1.0)
        assert issue is not None
        assert "left" in issue

    def test_centered_content_no_clipping(self):
        img = _background_with_content()
        issue = _check_edge_clipping(img, 1.0)
        assert issue is None

    def test_pure_background_no_clipping(self):
        img = _solid_image(_BACKGROUND_COLOR)
        issue = _check_edge_clipping(img, 1.0)
        assert issue is None


# -----------------------------------------------------------------------
# Frozen frame detection
# -----------------------------------------------------------------------

class TestFrozenFrames:
    def test_identical_frames_detected(self):
        img_a = _background_with_content()
        img_b = _background_with_content()  # identical
        issue = _check_frozen_frames(img_a, img_b, 1.0, 3.0)
        assert issue is not None
        assert "frozen" in issue.lower() or "identical" in issue.lower()

    def test_different_frames_not_flagged(self):
        img_a = _background_with_content()
        img_b = _solid_image((100, 50, 150))  # completely different
        issue = _check_frozen_frames(img_a, img_b, 1.0, 3.0)
        assert issue is None

    def test_slightly_different_not_flagged(self):
        """Small changes (compression artifacts) should not trigger."""
        img_a = _background_with_content()
        # Create a slightly modified copy
        img_b = img_a.copy()
        w, h = img_b.size
        # Change ~5% of pixels significantly
        for x in range(0, w, 5):
            for y in range(0, h, 5):
                r, g, b = img_b.getpixel((x, y))
                img_b.putpixel((x, y), (min(255, r + 50), g, b))
        issue = _check_frozen_frames(img_a, img_b, 1.0, 3.0)
        assert issue is None


def _text_like_frame(size: tuple[int, int] = (1920, 1080)) -> Image.Image:
    """Sparse white-on-dark strokes, like the pipeline's text scenes."""
    img = Image.new("RGB", size, _BACKGROUND_COLOR)
    d = ImageDraw.Draw(img)
    for i in range(8):
        d.line((300, 300 + i * 60, 1500 - i * 40, 300 + i * 60), fill=(235, 235, 235), width=4)
    return img


class TestFrozenCalibration:
    """R30: numbers behind _FROZEN_MAX_CHANGED, measured on 1080p synthetic frames.

    identical / +-2 noise: 0.0 changed; one 120px stroke: ~0.0005; the real
    Section01Scene pair (sparse text appearing): 0.0039; a 100px dot moving: ~0.008.
    """

    def test_static_pair_is_frozen_even_with_compression_noise(self):
        a = _text_like_frame()
        rng = np.random.default_rng(0)
        noisy = np.asarray(a, dtype=np.int16) + rng.integers(-2, 3, (1080, 1920, 3))
        b = Image.fromarray(np.clip(noisy, 0, 255).astype(np.uint8))
        assert _check_frozen_frames(a, a.copy(), 1.0, 3.0) is not None
        assert _check_frozen_frames(a, b, 1.0, 3.0) is not None

    def test_sparse_text_animation_is_not_frozen(self):
        """Section01Scene regression: 0.39% of pixels changed was flagged at 0.98."""
        a = _text_like_frame()
        b = a.copy()
        d = ImageDraw.Draw(b)
        # one 4px-thick, 1000px-long stroke is about 0.4% of a 1080p frame
        d.line((200, 850, 1200, 850), fill=(255, 255, 255), width=4)
        frac = _changed_fraction(_small_array(a), _small_array(b))
        assert 0.003 < frac < 0.006
        assert _check_frozen_frames(a, b, 1.5, 3.0) is None

    def test_tiny_deliberate_stroke_is_not_frozen(self):
        a = _text_like_frame()
        b = a.copy()
        ImageDraw.Draw(b).line((300, 800, 420, 800), fill=(255, 255, 255), width=4)
        assert _check_frozen_frames(a, b, 1.0, 3.0) is None

    def test_moving_dot_is_not_frozen(self):
        a = _text_like_frame()
        b, c = a.copy(), a.copy()
        ImageDraw.Draw(b).ellipse((900, 500, 1000, 600), fill=(255, 200, 0))
        ImageDraw.Draw(c).ellipse((1000, 500, 1100, 600), fill=(255, 200, 0))
        assert _check_frozen_frames(b, c, 1.0, 3.0) is None

    def test_size_mismatch_is_ignored(self):
        assert _check_frozen_frames(_solid_image((9, 9, 9), (10, 10)), _solid_image((9, 9, 9), (20, 20)), 0, 1) is None

    def test_1080p_pair_is_fast(self):
        """The old pure-Python loop took 1.3-1.9s per 1080p pair; numpy is ~tens of ms."""
        a, b = _text_like_frame(), _text_like_frame()
        start = time.perf_counter()
        for _ in range(5):
            _check_frozen_frames(a, b, 1.0, 3.0)
            _check_edge_clipping(a, 1.0)
            _check_black_frame(a, 1.0)
        assert time.perf_counter() - start < 2.0


class TestNumpyUnavailable:
    def test_pixel_check_raises_clear_error(self, monkeypatch):
        monkeypatch.setattr(frame_checker, "_HAS_NUMPY", False)
        with pytest.raises(RuntimeError, match="numpy"):
            _check_frozen_frames(_solid_image((1, 1, 1)), _solid_image((1, 1, 1)), 0, 1)

    def test_check_frames_skips_with_warning(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setattr(frame_checker, "_HAS_NUMPY", False)
        video = tmp_path / "v.mp4"
        video.write_bytes(b"\x00")
        with caplog.at_level("WARNING"):
            result = frame_checker.check_frames(str(video))
        assert result.skipped is True and result.ok is True
        assert "numpy" in caplog.text


# -----------------------------------------------------------------------
# FrameCheckResult dataclass
# -----------------------------------------------------------------------

class TestFrameCheckResult:
    def test_ok_result(self):
        r = FrameCheckResult(ok=True)
        assert r.ok
        assert r.issues_text == ""

    def test_issues_text(self):
        r = FrameCheckResult(ok=False, issues=["issue 1", "issue 2"])
        assert not r.ok
        assert "issue 1" in r.issues_text
        assert "issue 2" in r.issues_text

    def test_skipped(self):
        r = FrameCheckResult(ok=True, skipped=True)
        assert r.ok
        assert r.skipped


# -----------------------------------------------------------------------
# Regression: the extraction path must actually be callable
# -----------------------------------------------------------------------
#
# Every test above exercises a pure image-analysis helper, which is why 791
# passing tests never noticed that `_extract_frame_pil` raised NameError on
# every call: `subprocess` was imported only inside a different function, so
# the module-level reference was unbound. The broad `except Exception` caught
# it and logged below the CLI's level, so Tier 1 validation silently reported
# success while doing nothing.
#
# These tests cover the seam between this module and the outside world rather
# than the arithmetic inside it.

def test_module_imports_subprocess_at_module_level():
    """The extraction path references subprocess outside any function."""
    import manimgen.validator.frame_checker as fc

    assert hasattr(fc, "subprocess"), (
        "frame_checker calls subprocess.run at module scope; importing it only "
        "inside a helper leaves that reference unbound and makes every frame "
        "extraction fail silently"
    )


def test_extract_frame_invokes_ffmpeg_and_returns_image(mocker, tmp_path):
    """A successful ffmpeg call yields a PIL image, not None."""
    from manimgen.validator import frame_checker as fc

    # Write a real PNG where the extractor expects ffmpeg to have written one.
    captured: dict[str, str] = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        _solid_image((10, 20, 30)).save(cmd[-1])
        return mocker.MagicMock(returncode=0)

    mocker.patch.object(fc.subprocess, "run", side_effect=fake_run)

    image = fc._extract_frame_pil("/tmp/does-not-matter.mp4", 1.5)

    assert image is not None, "extraction returned None on a successful ffmpeg run"
    assert image.size == (320, 180)
    assert captured["cmd"][0] == "ffmpeg"
    assert "1.5" in captured["cmd"], "requested timestamp not passed to ffmpeg"


def test_extract_frame_returns_none_when_ffmpeg_fails(mocker):
    """A non-zero ffmpeg exit is handled, not raised."""
    from manimgen.validator import frame_checker as fc

    mocker.patch.object(
        fc.subprocess, "run", return_value=mocker.MagicMock(returncode=1)
    )
    assert fc._extract_frame_pil("/tmp/missing.mp4", 0.5) is None


def test_extraction_failure_is_logged_at_warning(mocker, caplog):
    """A programming error in the extraction path must be visible by default.

    Logging this at debug is what hid the missing import: the CLI runs at INFO,
    so the failure produced no output at all.
    """
    import logging

    from manimgen.validator import frame_checker as fc

    mocker.patch.object(fc.subprocess, "run", side_effect=RuntimeError("boom"))

    with caplog.at_level(logging.WARNING):
        assert fc._extract_frame_pil("/tmp/x.mp4", 0.25) is None

    assert any(
        record.levelno >= logging.WARNING and "Frame extraction failed" in record.message
        for record in caplog.records
    ), "extraction failure was not surfaced at WARNING or above"
