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

logger = logging.getLogger(__name__)

# Try to import PIL — fallback gracefully if not installed
try:
    from PIL import Image, ImageStat
    _HAS_PIL = True
except ImportError:
    _HAS_PIL = False
    logger.debug("[frame_checker] PIL not installed — deterministic checks disabled")


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

_BLACK_THRESHOLD = 15          # mean pixel value below this → "black frame"
_EDGE_MARGIN_PX = 12          # pixels from edge to check for clipping
_EDGE_BRIGHTNESS_THRESHOLD = 30  # pixels brighter than this near edges → clipping risk
_FROZEN_SIMILARITY = 0.98     # fraction of identical pixels for "frozen" detection
_BACKGROUND_COLOR = (28, 28, 28)  # #1C1C1C — the pipeline's dark background


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
                "ffmpeg", "-y",
                "-ss", str(timestamp),
                "-i", video_path,
                "-frames:v", "1",
                "-q:v", "2",
                tmp_path,
            ],
            capture_output=True,
            timeout=15,
        )
        if result.returncode != 0 or not os.path.exists(tmp_path):
            return None
        return Image.open(tmp_path).convert("RGB")
    except Exception as exc:
        logger.debug("[frame_checker] Frame extraction failed at %.2fs: %s", timestamp, exc)
        return None
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _get_video_duration(video_path: str) -> float | None:
    """Return video duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_black_frame(img: "Image.Image", timestamp: float) -> str | None:
    """Return an issue string if the frame is effectively black."""
    stat = ImageStat.Stat(img)
    mean_brightness = sum(stat.mean) / 3  # average of R, G, B means

    # Allow for the dark background (#1C1C1C ≈ 28)
    if mean_brightness < _BLACK_THRESHOLD:
        return (
            f"ISSUE: Black/empty frame at t={timestamp:.1f}s (mean brightness {mean_brightness:.0f}) | "
            f"CAUSE: Scene likely FadeOut'd all elements before this point, "
            f"or no objects were added | "
            f"FIX: Ensure visual continuity — never FadeOut everything until the final cue"
        )
    return None


def _check_edge_clipping(img: "Image.Image", timestamp: float) -> str | None:
    """Return an issue string if non-background content appears near frame edges."""
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
    bg_r, bg_g, bg_b = _BACKGROUND_COLOR

    for edge_name, edge_img in edges.items():
        pixels = list(edge_img.getdata())
        bright_count = 0
        for r, g, b in pixels:
            # Count pixels that are significantly brighter than background
            if (abs(r - bg_r) + abs(g - bg_g) + abs(b - bg_b)) > _EDGE_BRIGHTNESS_THRESHOLD * 3:
                bright_count += 1
        # If more than 5% of edge pixels are bright, something may be clipped
        if bright_count > len(pixels) * 0.05:
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

    pixels_a = list(img_a.getdata())
    pixels_b = list(img_b.getdata())
    total = len(pixels_a)

    if total == 0:
        return None

    same = 0
    for pa, pb in zip(pixels_a, pixels_b):
        # Count pixels that are identical (within a small tolerance for compression)
        if abs(pa[0] - pb[0]) + abs(pa[1] - pb[1]) + abs(pa[2] - pb[2]) < 15:
            same += 1

    similarity = same / total
    if similarity > _FROZEN_SIMILARITY:
        return (
            f"ISSUE: Frames at t={ts_a:.1f}s and t={ts_b:.1f}s are {similarity:.0%} identical — "
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
            "ffmpeg", "-i", video_path,
            "-vf", r"select=gt(scene\,0.35),showinfo",
            "-vsync", "vfr", "-f", "null", "-",
        ]
        result = _sp.run(cmd, capture_output=True, text=True, timeout=30)
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

    if not os.path.exists(video_path):
        return FrameCheckResult(ok=True, skipped=True)

    duration = _get_video_duration(video_path)
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
        logger.info("[frame_checker] Found %d issue(s) in %s", len(unique_issues), video_path)
        return FrameCheckResult(ok=False, issues=unique_issues)

    return FrameCheckResult(ok=True)
