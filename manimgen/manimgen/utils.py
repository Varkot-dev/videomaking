"""Shared utilities used across multiple manimgen modules."""

import base64
import glob
import hashlib
import os
import re
from typing import Any

# A section id must be safe to interpolate into a filesystem path AND into a
# Python class name. The planner emits LLM-controlled ids, so an id like
# "../../etc/cron.d/x" or "a/b" would otherwise flow into os.path.join sinks
# that write a .py file manimgl then EXECUTES — i.e. arbitrary write + exec.
# Allow only lowercase alphanumerics and underscore; everything else becomes
# "_". (See sanitize_section_id.)
_SECTION_ID_ALLOWED = re.compile(r"[^a-z0-9_]")
_MAX_SECTION_ID_LEN = 64


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


def sanitize_section_id(raw_id: Any, idx: int = 0) -> str:
    """Coerce an untrusted section id into a path/class-name-safe slug.

    The planner's section ids originate from LLM output and flow unsanitized
    into ``os.path.join`` sinks (cli.py, fallback.py, scene_generator.py) that
    write a ``.py`` file manimgl then executes. An id containing ``/`` or
    ``..`` is therefore a path-traversal → arbitrary-write+exec primitive.

    Rules (deterministic, no I/O):
      - lowercase, then replace every char outside ``[a-z0-9_]`` with ``_``
      - truncate to 64 chars
      - empty result → ``section_{idx:02d}``

    This is idempotent: a slug that is already safe passes through unchanged
    (apart from case), so applying it at the parse boundary AND again at each
    filesystem sink (defense in depth) never corrupts a good id.

    Collision handling (e.g. ``a/b`` and ``a_b`` both → ``a_b``) is the
    caller's responsibility at parse time; see
    ``lesson_planner._sanitize_section_ids`` which appends a content hash
    suffix when a sanitized id is not unique.
    """
    text = "" if raw_id is None else str(raw_id)
    slug = _SECTION_ID_ALLOWED.sub("_", text.lower())[:_MAX_SECTION_ID_LEN]
    if not slug:
        slug = f"section_{idx:02d}"
    return slug


def safe_section_id(section: dict, idx: int = 0) -> str:
    """Return a sanitized id for a section dict (defense-in-depth at sinks).

    Reads ``section['id']`` (or ``section_{idx:02d}`` when absent) and runs it
    through :func:`sanitize_section_id`. Filesystem sinks call this instead of
    using ``section['id']`` raw, so even an un-sanitized resume path (loading a
    legacy/poisoned ``plan.json``) cannot traverse.
    """
    raw = section.get("id") if isinstance(section, dict) else None
    if raw is None:
        return f"section_{idx:02d}"
    return sanitize_section_id(raw, idx)


def section_class_name(section: dict) -> str:
    """Derive the ManimGL Scene class name from a section dict.

    The id is sanitized first so a traversal-laden id can never produce a
    class name (and, downstream, a file path) containing ``/`` or ``..``.
    """
    safe_id = safe_section_id(section)
    return safe_id.replace("_", " ").title().replace(" ", "") + "Scene"


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
