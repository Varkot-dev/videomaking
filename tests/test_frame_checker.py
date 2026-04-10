"""Tests for the deterministic frame checker (Tier 1 visual validation)."""
from __future__ import annotations

import pytest

# Frame checker functions are tested at the unit level — we create fake PIL
# images instead of rendering real video (that would require manimgl + ffmpeg).
try:
    from PIL import Image
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False

from manimgen.validator.frame_checker import (
    FrameCheckResult,
    _check_black_frame,
    _check_edge_clipping,
    _check_frozen_frames,
    _BACKGROUND_COLOR,
)

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

    def test_background_color_not_flagged(self):
        """The pipeline's #1C1C1C background (28,28,28) should NOT be flagged."""
        img = _solid_image(_BACKGROUND_COLOR)
        issue = _check_black_frame(img, 1.0)
        assert issue is None

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
