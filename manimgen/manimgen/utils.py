"""Shared utilities used across multiple manimgen modules."""

import base64
import glob
import os
from typing import Any


def safe_probe_duration(data: Any) -> float | None:
    """Safely extract a media duration from parsed ffprobe JSON.

    ffprobe emits the string "N/A" (or omits the key entirely) for the
    format-level duration on some containers, so a naive
    ``float(data["format"]["duration"])`` raises ``ValueError`` or
    ``KeyError`` and crashes the pipeline. This mirrors the safe-parse
    semantics of ``assembler._video_duration``: tolerate a missing key,
    "N/A", empty string, ``None``, and non-numeric values, returning
    ``None`` when no usable duration is present so the caller can apply
    its own fallback.

    Args:
        data: The dict returned by ``json.loads`` on ffprobe's
            ``-of json`` output (or anything not shaped like it).

    Returns:
        The duration in seconds as a float, or ``None`` if unreadable.
    """
    if not isinstance(data, dict):
        return None
    fmt = data.get("format")
    if not isinstance(fmt, dict):
        return None
    raw = fmt.get("duration")
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or text == "N/A":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def strip_fencing(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def section_class_name(section: dict) -> str:
    """Derive the ManimGL Scene class name from a section dict."""
    return section["id"].replace("_", " ").title().replace(" ", "") + "Scene"


def load_reference_frames() -> list[str]:
    """Load the gold standard 1080p ManimGL aesthetic reference frames as base64."""
    here = os.path.dirname(__file__)
    ref_dir = os.path.join(here, "reference_frames")
    pngs = glob.glob(os.path.join(ref_dir, "*.png"))

    frames = []
    for path in sorted(pngs):
        with open(path, "rb") as f:
            frames.append(base64.b64encode(f.read()).decode("utf-8"))
    return frames
