"""
Layout checker: renders a frame from a rendered video and uses LLM vision
to detect visual issues (overlapping elements, text cut off, poor spacing).

Returns structured feedback that the retry loop can feed back to the LLM.
"""

import base64
import logging
import os
import subprocess
import tempfile

from manimgen.llm import chat

logger = logging.getLogger(__name__)


def _load_layout_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "layout_checker_system.md")) as f:
        return f.read()


def _extract_frame(video_path: str, timestamp: float = 1.0) -> str | None:
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
            logger.warning("[layout_checker] ffmpeg frame extract failed: %s", result.stderr)
            return None

        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as exc:
        logger.warning("[layout_checker] Frame extraction error: %s", exc)
        return None
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def check_layout(video_path: str) -> dict:
    """
    Check a rendered scene video for layout issues using LLM vision.

    Returns:
        {
            "ok": bool,         True if no issues found
            "issues": str,      bullet list of problems, or "" if ok
            "skipped": bool,    True if check could not run (no ffmpeg, no frame, etc.)
        }
    """
    if not os.path.exists(video_path):
        logger.warning("[layout_checker] Video not found: %s", video_path)
        return {"ok": True, "issues": "", "skipped": True}

    # Try a frame at 1s, then 0.5s as fallback (short scenes may not have 1s of content)
    frame_b64 = _extract_frame(video_path, timestamp=1.0)
    if frame_b64 is None:
        frame_b64 = _extract_frame(video_path, timestamp=0.5)

    if frame_b64 is None:
        logger.warning("[layout_checker] Could not extract any frame from %s", video_path)
        return {"ok": True, "issues": "", "skipped": True}

    try:
        response = chat(
            system=_load_layout_system_prompt(),
            user="Review this animation frame for layout issues.",
            images=[frame_b64],
        )
    except Exception as exc:
        logger.warning("[layout_checker] LLM call failed: %s", exc)
        return {"ok": True, "issues": "", "skipped": True}

    clean = response.strip()
    if clean.upper() == "OK":
        return {"ok": True, "issues": "", "skipped": False}

    return {"ok": False, "issues": clean, "skipped": False}
