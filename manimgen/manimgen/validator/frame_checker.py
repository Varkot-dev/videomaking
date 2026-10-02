"""
Frame checker — deterministic, zero-cost visual validation using PIL.

Extracts frames from a rendered video and checks for common defects without
any LLM calls:

- Black frame detection (scene showing empty/dark screen)
- Frozen frame detection (animation isn't moving)
- Edge clipping detection (content cut off at screen edges)

This is Tier 1 of the two-tier visual validation system. It runs on every
render attempt regardless of LLM budget.

Tier 2 (LLM vision via layout_checker.py) handles nuanced defects that
can't be caught by pixel analysis (wrong colors, overlapping labels, etc.).
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field

from manimgen.utils import probe_video_duration

logger = logging.getLogger(__name__)

# Try to import PIL — fallback gracefully if not installed
try:
    from PIL import Image, ImageStat

    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    logger.debug("[frame_checker] PIL not installed — deterministic checks disabled")

# numpy ships with manimgl, so it is always present in a working install. It is
# still imported defensively so a broken environment degrades to a clear
# warning (checks skipped) instead of an ImportError at import time.
try:
    import numpy as np

    _HAS_NUMPY = True
except ImportError:
    np = None  # type: ignore[assignment]
    _HAS_NUMPY = False
    logger.debug("[frame_checker] numpy not installed — deterministic checks disabled")


def _require_numpy() -> None:
    """Raise a clear error when a pixel check is called without numpy."""
    if not _HAS_NUMPY:
        raise RuntimeError(
            "frame_checker needs numpy for pixel comparison but it is not "
            "installed. numpy is a dependency of manimgl; reinstall it with "
            "'pip install numpy'."
        )


@dataclass
class FrameCheckResult:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def issues_text(self) -> str:
        return "\n".join(self.issues)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_BLACK_THRESHOLD = 15  # mean pixel value below this → "black frame"
_EDGE_MARGIN_PX = 12  # pixels from edge to check for clipping
_EDGE_BRIGHTNESS_THRESHOLD = 30  # pixels brighter than this near edges → clipping risk
_BACKGROUND_COLOR = (28, 28, 28)  # #1C1C1C — the pipeline's dark background

# Pixel comparison runs on a frame downscaled (box filter) to this width.
_ANALYSIS_WIDTH = 480
# Per-pixel change tolerance (sum of |dR|+|dG|+|dB|) that absorbs compression noise.
_PIXEL_TOLERANCE = 15
# Two frames are "frozen" when at most this fraction of pixels changed.
# Measured on synthetic 1080p frames (see tests/test_frame_checker.py):
#   identical or +-2 noise  -> 0.00000 changed
#   one 120px text stroke    -> 0.00048 changed (deliberate tiny animation)
#   real Section01Scene pair -> 0.0039  changed (sparse white-on-dark text)
#   a 100px dot moving       -> 0.0083  changed
# 0.0002 sits above compression noise and below the smallest deliberate motion.
_FROZEN_MAX_CHANGED = 0.0002
# A frame is "empty" when fewer than this fraction of pixels differ from the
# background. A flat #1C1C1C frame measures 0.0; any title or diagram is > 0.005.
_EMPTY_MAX_NONBG = 0.0005


# ---------------------------------------------------------------------------
# Frame extraction
# ---------------------------------------------------------------------------


def _extract_frame_pil(video_path: str, timestamp: float) -> "Image.Image | None":
    """Extract a single frame as a PIL Image."""
    if not _HAS_PIL:
        return None

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                str(timestamp),
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-q:v",
                "2",
                tmp_path,
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0 or not os.path.exists(tmp_path):
            return None
        # Close the handle before the finally block unlinks the file: Windows
        # refuses to delete a file that is still open.
        with Image.open(tmp_path) as img:
            return img.convert("RGB")
    except Exception as exc:
        # Warning, not debug. A missing import here raised NameError on every
        # call, which this handler swallowed and logged below the CLI's INFO
        # level — so Tier 1 frame validation silently passed for months while
        # appearing to work. An exception that indicates a programming error
        # rather than a bad video must be visible by default.
        logger.warning(
            "[frame_checker] Frame extraction failed at %.2fs: %s: %s",
            timestamp,
            type(exc).__name__,
            exc,
        )
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def _small_array(img: "Image.Image") -> "np.ndarray":
    """Return the frame as an int16 H x W x 3 array, box-downscaled when large."""
    _require_numpy()
    w, h = img.size
    if w > _ANALYSIS_WIDTH:
        img = img.resize(
            (_ANALYSIS_WIDTH, max(1, round(h * _ANALYSIS_WIDTH / w))), Image.BOX
        )
    return np.asarray(img, dtype=np.int16)


def _changed_fraction(a: "np.ndarray", b: "np.ndarray") -> float:
    """Fraction of pixels whose summed channel difference reaches the tolerance."""
    return float((np.abs(a - b).sum(axis=2) >= _PIXEL_TOLERANCE).mean())


def _check_black_frame(img: "Image.Image", timestamp: float) -> str | None:
    """Return an issue string if the frame is effectively black or empty.

    "Empty" means almost no pixel differs from the pipeline background: the mean
    brightness test alone can never fire on #1C1C1C (28 > _BLACK_THRESHOLD).
    """
    stat = ImageStat.Stat(img)
    mean_brightness = sum(stat.mean) / 3  # average of R, G, B means
    nonbg = _changed_fraction(
        _small_array(img), np.array(_BACKGROUND_COLOR, dtype=np.int16)
    )

    if mean_brightness < _BLACK_THRESHOLD or nonbg < _EMPTY_MAX_NONBG:
        return (
            f"ISSUE: Black/empty frame at t={timestamp:.1f}s (mean brightness {mean_brightness:.0f}, {nonbg:.2%} non-background pixels) | "
            f"CAUSE: Scene likely FadeOut'd all elements before this point, "
            f"or no objects were added | "
            f"FIX: Ensure visual continuity — never FadeOut everything until the final cue"
        )
    return None


def _check_edge_clipping(img: "Image.Image", timestamp: float) -> str | None:
    """Return an issue string if non-background content appears near frame edges."""
    _require_numpy()
    w, h = img.size
    margin = min(_EDGE_MARGIN_PX, w // 20, h // 20)

    # Check all four edges for bright (non-background) pixels
    edges = {
        "top": img.crop((0, 0, w, margin)),
        "bottom": img.crop((0, h - margin, w, h)),
        "left": img.crop((0, 0, margin, h)),
        "right": img.crop((w - margin, 0, w, h)),
    }

    clipped_edges = []
    bg = np.array(_BACKGROUND_COLOR, dtype=np.int16)

    for edge_name, edge_img in edges.items():
        arr = np.asarray(edge_img, dtype=np.int16)
        # Count pixels that are significantly brighter than background
        bright = np.abs(arr - bg).sum(axis=2) > _EDGE_BRIGHTNESS_THRESHOLD * 3
        # If more than 5% of edge pixels are bright, something may be clipped
        if bright.sum() > bright.size * 0.05:
            clipped_edges.append(edge_name)

    if clipped_edges:
        edges_str = ", ".join(clipped_edges)
        return (
            f"ISSUE: Content near {edges_str} edge(s) at t={timestamp:.1f}s — "
            f"element may be cut off | "
            f"CAUSE: Object positioned outside frame bounds "
            f"(x outside [-7,7] or y outside [-4,4]) | "
            f"FIX: Check .to_edge() buff values and .shift() magnitudes; "
            f"ensure all objects are within the visible frame"
        )
    return None


def _check_frozen_frames(
    img_a: "Image.Image",
    img_b: "Image.Image",
    ts_a: float,
    ts_b: float,
) -> str | None:
    """Return an issue string if two frames are nearly identical (frozen animation)."""
    if img_a.size != img_b.size:
        return None

    arr_a = _small_array(img_a)
    arr_b = _small_array(img_b)
    if arr_a.size == 0:
        return None

    changed = _changed_fraction(arr_a, arr_b)
    similarity = 1.0 - changed
    if changed <= _FROZEN_MAX_CHANGED:
        return (
            f"ISSUE: Frames at t={ts_a:.1f}s and t={ts_b:.1f}s are {similarity:.2%} identical — "
            f"animation appears frozen | "
            f"CAUSE: Director likely used self.wait() for too long without any animation, "
            f"or all play() calls have very short run_time | "
            f"FIX: Add visual activity during long waits (annotation, highlight, label update)"
        )
    return None


# ---------------------------------------------------------------------------
# Scene-guided frame sampling
# ---------------------------------------------------------------------------


def _scene_guided_timestamps(video_path: str, duration: float) -> list[float]:
    """Return frame timestamps guided by detected scene cuts.

    Runs ffmpeg select filter to find scene boundaries, then samples:
    - First frame of each new scene
    - Midpoint of scenes longer than 3s
    Caps at 8 frames; falls back to [0.25, 0.50, 0.75] * duration on error.
    """
    import re as _re
    import subprocess as _sp

    fallback = [duration * 0.25, duration * 0.50, duration * 0.75]
    try:
        cmd = [
            "ffmpeg",
            "-i",
            video_path,
            "-vf",
            r"select=gt(scene\,0.35),showinfo",
            "-vsync",
            "vfr",
            "-f",
            "null",
            "-",
        ]
        result = _sp.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        pts_times = [float(m) for m in _re.findall(r"pts_time:([\d.]+)", result.stderr)]
        if not pts_times:
            return fallback
        timestamps: list[float] = []
        prev = 0.0
        for t in pts_times:
            timestamps.append(t)
            seg_len = t - prev
            if seg_len > 3.0:
                timestamps.append(prev + seg_len / 2)
            prev = t
        timestamps.sort()
        return timestamps[:8] if timestamps else fallback
    except Exception:
        return fallback


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def check_frames(video_path: str) -> FrameCheckResult:
    """Run deterministic frame checks on a rendered video.

    Samples frames at 25%, 50%, and 75% of video duration and checks each
    for black frames, edge clipping, and frozen animation.

    Returns a FrameCheckResult with ok=True if no issues, or ok=False with
    structured ISSUE|CAUSE|FIX lines matching the layout_checker format.
    """
    if not _HAS_PIL:
        return FrameCheckResult(ok=True, skipped=True)

    if not _HAS_NUMPY:
        logger.warning(
            "[frame_checker] numpy not installed — frame checks skipped "
            "(pip install numpy)"
        )
        return FrameCheckResult(ok=True, skipped=True)

    if not os.path.exists(video_path):
        return FrameCheckResult(ok=True, skipped=True)

    duration = probe_video_duration(video_path)
    if not duration or duration < 0.5:
        return FrameCheckResult(ok=True, skipped=True)

    timestamps = _scene_guided_timestamps(video_path, duration)
    frames: list[tuple[float, "Image.Image"]] = []

    for ts in timestamps:
        img = _extract_frame_pil(video_path, ts)
        if img is not None:
            frames.append((ts, img))

    if not frames:
        return FrameCheckResult(ok=True, skipped=True)

    issues: list[str] = []

    # Check each frame individually
    for ts, img in frames:
        black_issue = _check_black_frame(img, ts)
        if black_issue:
            issues.append(black_issue)

        clip_issue = _check_edge_clipping(img, ts)
        if clip_issue:
            issues.append(clip_issue)

    # Check for frozen animation between frames
    for i in range(len(frames) - 1):
        ts_a, img_a = frames[i]
        ts_b, img_b = frames[i + 1]
        frozen_issue = _check_frozen_frames(img_a, img_b, ts_a, ts_b)
        if frozen_issue:
            issues.append(frozen_issue)

    # Deduplicate issues (edge clipping may appear at multiple timestamps)
    seen: set[str] = set()
    unique_issues: list[str] = []
    for issue in issues:
        # Normalize for dedup — strip timestamps
        key = issue.split("|")[0].split("at t=")[0].strip()
        if key not in seen:
            seen.add(key)
            unique_issues.append(issue)

    if unique_issues:
        logger.info(
            "[frame_checker] Found %d issue(s) in %s", len(unique_issues), video_path
        )
        return FrameCheckResult(ok=False, issues=unique_issues)

    return FrameCheckResult(ok=True)
