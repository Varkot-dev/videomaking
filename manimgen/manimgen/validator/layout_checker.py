"""
Layout checker: samples multiple frames from a rendered video and uses LLM vision
to detect visual issues (overlapping elements, stale bounding boxes, color bleed,
ghost elements from swaps/transitions).

Returns structured feedback (ISSUE/CAUSE/FIX lines) that the retry loop acts on.
"""

import base64
import logging
import os
import re
import subprocess
import tempfile

from manimgen.llm import chat
from manimgen.utils import load_reference_frames, probe_video_duration

logger = logging.getLogger(__name__)

# The first-pass vision check (validate_render) is OFF by default: its verdict
# was never enforced, so it cost one vision call per section for nothing.
# Set this to a truthy value to re-enable it once the judge has been measured.
FIRST_PASS_ENV_VAR = "MANIMGEN_FIRST_PASS_LAYOUT"
_FALSEY = frozenset({"", "0", "false", "off", "no"})


def first_pass_layout_enabled() -> bool:
    """True when MANIMGEN_FIRST_PASS_LAYOUT opts in to the first-pass vision check."""
    return os.environ.get(FIRST_PASS_ENV_VAR, "").strip().lower() not in _FALSEY


_ISSUE_LINE = re.compile(r"^[\s>*\-\u2022\d.)]*\**\s*ISSUE\s*:", re.IGNORECASE)
_OK_REPLY = re.compile(r"^[\s*_`>#\-]*OK\b", re.IGNORECASE)


def parse_layout_verdict(response: str) -> tuple[str, str]:
    """Classify a layout reply as ``("ok", "")``, ``("issues", text)`` or ``("unverified", "")``.

    Only lines that carry an ``ISSUE:`` marker count as defects. A reply that
    starts with OK (any case, markdown or trailing punctuation) and has no
    ISSUE line is clean. Anything else (prose with no ISSUE line, "No defects
    found") cannot be trusted either way, so it is unverified rather than a
    blind defect.
    """
    lines = [ln.strip() for ln in response.strip().splitlines()]
    issues = [ln for ln in lines if _ISSUE_LINE.match(ln)]
    if issues:
        return "issues", "\n".join(issues)
    if _OK_REPLY.match(response.strip()):
        return "ok", ""
    return "unverified", ""


def _load_layout_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(
        os.path.join(here, "prompts", "layout_checker_system.md"), encoding="utf-8"
    ) as f:
        return f.read()


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
            timeout=30,
        )
        if result.returncode != 0 or not os.path.exists(tmp_path):
            logger.warning(
                "[layout_checker] ffmpeg frame extract failed at %.2fs: %s",
                timestamp,
                result.stderr,
            )
            return None

        with open(tmp_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    except Exception as exc:
        logger.warning(
            "[layout_checker] Frame extraction error at %.2fs: %s", timestamp, exc
        )
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
    duration = probe_video_duration(video_path)

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
            "skipped": bool,  True if check could not run or the reply was
                              unparseable (treated as UNVERIFIED by callers)
        }
    """
    if not os.path.exists(video_path):
        logger.warning("[layout_checker] Video not found: %s", video_path)
        return {"ok": True, "issues": "", "skipped": True}

    frames = _sample_frames(video_path)

    if not frames:
        logger.warning(
            "[layout_checker] Could not extract any frames from %s", video_path
        )
        return {"ok": True, "issues": "", "skipped": True}

    logger.debug("[layout_checker] Checking %d frames from %s", len(frames), video_path)

    ref_frames = load_reference_frames()
    if ref_frames:
        user = (
            f"The FIRST {len(ref_frames)} images are style references only. "
            f"The REMAINING {len(frames)} images are candidate frames sampled "
            "across the video timeline.\n\n"
            "Review the candidate frames for defects. Use the references only to "
            "judge what clean looks like; do not report style differences."
        )
    else:
        user = (
            f"The {len(frames)} images are candidate frames sampled across the "
            "video timeline.\n\nReview them for defects."
        )

    try:
        response = chat(
            system=_load_layout_system_prompt(),
            user=user,
            images=ref_frames + frames,
            role="layout_check",
        )
    except Exception as exc:
        logger.warning("[layout_checker] LLM call failed: %s", exc)
        return {"ok": True, "issues": "", "skipped": True, "frames": []}

    # Some providers return None on content-filter blocks or empty replies.
    # Treat those as "no verdict available" — skip rather than crash downstream.
    if not response:
        logger.warning("[layout_checker] LLM returned empty response — skipping")
        return {"ok": True, "issues": "", "skipped": True, "frames": []}

    verdict, issues = parse_layout_verdict(response)
    if verdict == "ok":
        return {"ok": True, "issues": "", "skipped": False, "frames": frames}
    if verdict == "unverified":
        logger.warning(
            "[layout_checker] Unparseable reply (no ISSUE lines, not OK) — unverified"
        )
        return {
            "ok": True,
            "issues": "",
            "skipped": True,
            "unverified": True,
            "frames": [],
        }

    return {"ok": False, "issues": issues, "skipped": False, "frames": frames}
