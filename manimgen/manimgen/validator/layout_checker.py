"""
Layout checker: samples multiple frames from a rendered video and uses LLM vision
to detect visual issues (overlapping elements, stale bounding boxes, color bleed,
ghost elements from swaps/transitions).

Returns structured feedback (ISSUE/CAUSE/FIX lines) that the retry loop acts on.
"""

import base64
import logging
import os
import subprocess
import tempfile

from manimgen.llm import chat
from manimgen.utils import load_reference_frames

logger = logging.getLogger(__name__)


def _load_layout_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "layout_checker_system.md")) as f:
        return f.read()


def _get_video_duration(video_path: str) -> float | None:
    """Return video duration in seconds via ffprobe, or None on failure."""
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
            timeout=15,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception as exc:
        logger.warning("[layout_checker] ffprobe duration failed: %s", exc)
    return None


def _extract_frame(video_path: str, timestamp: float) -> str | None:
    """
    Extract a single frame from a video at `timestamp` seconds.
    Returns base64-encoded PNG string, or None on failure.
    """
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
            timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(tmp_path):
            logger.warning("[layout_checker] ffmpeg frame extract failed at %.2fs: %s", timestamp, result.stderr)
            return None

        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as exc:
        logger.warning("[layout_checker] Frame extraction error at %.2fs: %s", timestamp, exc)
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def _sample_frames(video_path: str) -> list[str]:
    """
    Extract frames at 25%, 50%, and 75% of video duration.
    Falls back to fixed timestamps (0.5s, 1.0s) if duration cannot be determined.
    Returns a list of base64-encoded PNG strings (may be empty).
    """
    duration = _get_video_duration(video_path)

    if duration and duration > 0.5:
        timestamps = [duration * 0.25, duration * 0.5, duration * 0.75]
    else:
        # Short or unknown duration — try fixed fallbacks
        timestamps = [0.5, 1.0]

    frames = []
    for ts in timestamps:
        frame = _extract_frame(video_path, ts)
        if frame is not None:
            frames.append(frame)

    return frames


def check_layout(video_path: str) -> dict:
    """
    Check a rendered scene video for visual defects using LLM vision.

    Samples multiple frames across the video timeline (25%/50%/75% of duration)
    and sends all frames in a single LLM call. Returns structured feedback that
    the retry loop can act on directly.

    Returns:
        {
            "ok": bool,       True if no issues found
            "issues": str,    structured ISSUE/CAUSE/FIX lines, or "" if ok
            "skipped": bool,  True if check could not run
        }
    """
    if not os.path.exists(video_path):
        logger.warning("[layout_checker] Video not found: %s", video_path)
        return {"ok": True, "issues": "", "skipped": True}

    frames = _sample_frames(video_path)

    if not frames:
        logger.warning("[layout_checker] Could not extract any frames from %s", video_path)
        return {"ok": True, "issues": "", "skipped": True}

    logger.debug("[layout_checker] Checking %d frames from %s", len(frames), video_path)

    ref_frames = load_reference_frames()

    try:
        response = chat(
            system=_load_layout_system_prompt(),
            user=(
                f"The FIRST {len(ref_frames)} images are Gold Standard reference frames of the aesthetic you must enforce.\n"
                f"The REMAINING {len(frames)} images are candidate frames sampled across the video timeline.\n\n"
                "Review the candidate frames. Compare their layout, typography, negative space, and proportions against the Gold Standard references. "
                "If the candidates look cramped, misaligned, or amateurish compared to the references, reject them."
            ),
            images=ref_frames + frames,
        )
    except Exception as exc:
        logger.warning("[layout_checker] LLM call failed: %s", exc)
        return {"ok": True, "issues": "", "skipped": True, "frames": []}

    clean = response.strip()
    if clean.upper() == "OK":
        return {"ok": True, "issues": "", "skipped": False, "frames": frames}

    return {"ok": False, "issues": clean, "skipped": False, "frames": frames}
