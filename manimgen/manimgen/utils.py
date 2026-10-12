"""Shared utilities used across multiple manimgen modules."""

import base64
import contextlib
import glob
import os
import re
import subprocess
import time
import uuid
from collections.abc import Iterator
from typing import Any

# A section id must be safe to interpolate into a filesystem path AND into a
# Python class name. The planner emits LLM-controlled ids, so an id like
# "../../etc/cron.d/x" or "a/b" would otherwise flow into os.path.join sinks
# that write a .py file manimgl then EXECUTES — i.e. arbitrary write + exec.
# Allow only lowercase alphanumerics and underscore; everything else becomes
# "_". (See sanitize_section_id.)
_SECTION_ID_ALLOWED = re.compile(r"[^a-z0-9_]")
_MAX_SECTION_ID_LEN = 64


# os.replace onto a file another process holds open (a video player, an
# antivirus scan) raises PermissionError on Windows. The lock is usually brief,
# so retry a few times before giving up.
_REPLACE_ATTEMPTS = 5
_REPLACE_DELAY_SECONDS = 0.2


def replace_with_retry(src: str, dst: str) -> None:
    """``os.replace`` that retries briefly on PermissionError (Windows locks)."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY_SECONDS)


@contextlib.contextmanager
def atomic_output(final_path: str) -> Iterator[str]:
    """Yield a unique temp path next to ``final_path``; publish it on success.

    A killed or timed-out encoder leaves only the temp file, never a partial
    file at ``final_path`` that a later run could trust. On a clean exit the
    temp file is moved over ``final_path`` with ``os.replace`` (atomic, and
    replaces an existing file on Windows too); on any exception it is removed.
    The temp name keeps the final extension so ffmpeg still infers the muxer,
    and carries a random token so concurrent writers never share a name.
    """
    directory, name = os.path.split(final_path)
    stem, ext = os.path.splitext(name)
    tmp = os.path.join(directory, f"{stem}.{uuid.uuid4().hex[:8]}.part{ext}")
    try:
        yield tmp
        if not os.path.exists(tmp):
            raise FileNotFoundError(f"encoder produced no output file for {final_path}")
        replace_with_retry(tmp, final_path)
    finally:
        with contextlib.suppress(OSError):
            if os.path.exists(tmp):
                os.remove(tmp)


def ffmpeg_concat_line(path: str) -> str:
    """Return one ffmpeg concat-demuxer ``file '...'`` line for ``path``.

    The path is made absolute and written with forward slashes, which ffmpeg
    accepts on every platform, so Windows paths such as ``C:\\Users\\...``
    never reach the demuxer's backslash-escape parser. A single quote inside the
    path is escaped as ``'\\''`` (close quote, escaped quote, reopen), which is
    the quoting the concat demuxer documents. Write the list file as UTF-8 so
    non-ASCII directory names survive on Windows (whose default is cp1252).
    """
    p = os.path.abspath(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{p}'\n"


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


def probe_video_duration(video_path: str, timeout: int = 15) -> float | None:
    """Return video duration in seconds via ffprobe, or None on failure."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if result.returncode == 0:
            return float(result.stdout.strip())
    except Exception:
        pass
    return None


def strip_fencing(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


# How llm.py reports a used-up allowance from `claude -p` (its fatal markers).
_USAGE_LIMIT_MARKERS = ("usage limit", "limit reached", "credit balance")


def is_usage_stop(exc: BaseException) -> bool:
    """True when an LLM error means "stop the run now, resume later" (#72).

    That is the plan or paid-API guard (PaidApiBlockedError) or a `claude -p`
    call refused because the plan's usage limit is reached. Retrying either
    inside the run cannot help, so callers must re-raise it, not swallow it.
    """
    from manimgen.llm import PaidApiBlockedError

    if isinstance(exc, PaidApiBlockedError):
        return True
    text = str(exc).lower()
    return (
        isinstance(exc, RuntimeError)
        and "claude -p failed" in text
        and any(marker in text for marker in _USAGE_LIMIT_MARKERS)
    )


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
    """Load 1080p ManimGL aesthetic reference frames as base64, if any are present.

    These are sent to the vision model as style exemplars during layout review.

    **This directory ships empty.** It previously held 20 frames captured from
    3Blue1Brown videos. Manim itself is MIT licensed, but the rendered videos
    are not, so those frames were removed rather than redistributed — see
    NOTICE for the full record.

    An empty list is a supported state, not an error: both the layout checker
    and the retry path treat "no reference frames" as "skip style comparison".
    To restore the capability, render your own frames from ``manimgen/examples/`` and
    drop the PNGs here — which also yields exemplars matching this project's own
    visual conventions rather than someone else's.
    """
    here = os.path.dirname(__file__)
    ref_dir = os.path.join(here, "reference_frames")
    pngs = glob.glob(os.path.join(ref_dir, "*.png"))

    frames = []
    for path in sorted(pngs):
        with open(path, "rb") as f:
            frames.append(base64.b64encode(f.read()).decode("utf-8"))
    return frames
