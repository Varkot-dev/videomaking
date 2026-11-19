# TTS support — Phase 4
# Generates spoken narration audio from text using edge-tts (Microsoft Neural TTS).
#
# Available voices (set via config.yaml tts.voice):
#   "en-US-AndrewMultilingualNeural" — male, smoother/prosodic technical narration (default)
#   "en-US-GuyNeural"    — male, neutral
#   "en-US-JennyNeural"  — female, warm
#   "en-US-EricNeural"   — male, authoritative
#
# Word timestamps
# ---------------
# generate_narration() returns a WordTimestamps object alongside the audio path.
# Each entry is:
#   {"word": str, "start": float, "end": float}   (seconds, from audio start)
#
# These timestamps are the ground truth for animation cuing:
#   - "start" = when this word begins being spoken
#   - "end"   = when this word finishes (start + duration)
#
# The caller marks certain word indices as cue points. The time at which
# cue word[i] starts speaking is exactly when the next animation should begin.

import asyncio
import contextlib
import contextvars
import json
import logging
import os
import subprocess
import time
from dataclasses import dataclass

import edge_tts

from manimgen import config
from manimgen.utils import safe_probe_duration

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_tts_config() -> dict:
    """tts settings from the shared config loader (manimgen.config)."""
    return config.section("tts")


_TTS_CFG = _load_tts_config()

_DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
_DEFAULT_SPEED = "+5%"

# edge-tts talks to an unofficial Microsoft endpoint over the network, so a
# single call fails on flaky Wi-Fi or a slow proxy (#73). Three attempts, with
# these waits before the second and the third.
_TTS_ATTEMPTS = 3
_TTS_BACKOFF_SECONDS = (2, 5)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class WordTimestamp:
    word: str
    start: float  # seconds from audio start
    end: float  # seconds from audio start


# ---------------------------------------------------------------------------
# Core TTS function
# ---------------------------------------------------------------------------


async def _generate_async(
    text: str,
    output_path: str,
    voice: str,
    rate: str,
) -> list[WordTimestamp]:
    """Stream TTS, write audio to output_path, return word timestamps."""
    communicate = edge_tts.Communicate(
        text,
        voice,
        rate=rate,
        boundary="WordBoundary",
        # Optional tts.proxy in config.yaml; HTTPS_PROXY is honoured anyway.
        proxy=_TTS_CFG.get("proxy") or None,
    )

    audio_chunks: list[bytes] = []
    word_timestamps: list[WordTimestamp] = []

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            # edge-tts reports offsets in 100-nanosecond units
            start_sec = chunk["offset"] / 10_000_000
            duration_sec = chunk["duration"] / 10_000_000
            word_timestamps.append(
                WordTimestamp(
                    word=chunk["text"],
                    start=start_sec,
                    end=start_sec + duration_sec,
                )
            )

    with open(output_path, "wb") as f:
        f.write(b"".join(audio_chunks))

    return word_timestamps


def _generate_edge(
    text: str,
    output_path: str,
    voice: str | None = None,
) -> tuple[str, list[WordTimestamp]]:
    """Generate narration audio from text using edge-tts.

    Returns (audio_path, word_timestamps).

    word_timestamps is a list of WordTimestamp(word, start, end) in order,
    where start/end are seconds from the beginning of the audio file.
    These are used to cue animations: when word[i] starts speaking,
    the animation associated with that cue point begins.

    edge-tts is tried up to three times; empty audio or no word timestamps
    counts as a failure. Raises RuntimeError when every attempt failed.
    """
    if voice is None:
        voice = _TTS_CFG.get("voice", _DEFAULT_VOICE)
    rate = _TTS_CFG.get("speed", _DEFAULT_SPEED)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, _TTS_ATTEMPTS + 1):
        if attempt > 1:
            time.sleep(_TTS_BACKOFF_SECONDS[attempt - 2])
        try:
            timestamps = asyncio.run(_generate_async(text, output_path, voice, rate))
            # Words in, nothing out is a failed call (cues need the timings).
            if text.strip():
                if not os.path.exists(output_path) or not os.path.getsize(output_path):
                    raise RuntimeError("edge-tts returned no audio")
                if not timestamps:
                    raise RuntimeError("edge-tts returned no word timestamps")
            return output_path, timestamps
        except Exception as e:
            last_error = e
            logger.warning("[tts] attempt %d/%d failed: %s", attempt, _TTS_ATTEMPTS, e)
    raise RuntimeError(
        f"edge-tts failed after {_TTS_ATTEMPTS} attempts: {last_error}"
    ) from last_error


# ---------------------------------------------------------------------------
# Engine selection and the offline fallback
# ---------------------------------------------------------------------------

# Set once edge-tts has failed and the offline voice took over. From then on
# the run goes straight to the offline voice, so one video does not alternate
# between two voices, and a blocked network is not retried for every section.
_edge_unreachable = False

# Set once tts.engine is kokoro but the offline voice could not be set up
# (package missing, model files not obtainable). From then on the run uses
# edge-tts and never tries Kokoro again, so the two engines cannot loop.
_kokoro_unavailable = False


def reset_fallback() -> None:
    """Forget both fallbacks (a new run, or tests)."""
    global _edge_unreachable, _kokoro_unavailable
    _edge_unreachable = False
    _kokoro_unavailable = False


def _offline_voice():
    """The offline engine module, imported lazily (it needs numpy and requests)."""
    from manimgen.renderer import tts_kokoro

    return tts_kokoro


def _is_service_failure(exc: BaseException) -> bool:
    """True for a failure of the speech service or the network path to it."""
    import aiohttp
    from edge_tts import exceptions as edge_exc

    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(
            exc,
            (
                OSError,
                asyncio.TimeoutError,
                aiohttp.ClientError,
                edge_exc.EdgeTTSException,
            ),
        ):
            return True
        if isinstance(exc, RuntimeError) and (
            "no audio" in str(exc) or "no word timestamps" in str(exc)
        ):
            return True
        exc = exc.__cause__
    return False


_cue_hint: contextvars.ContextVar = contextvars.ContextVar("tts_cue_hint", default=None)


@contextlib.contextmanager
def cue_hint(cue_word_indices: list[int] | None):
    """Tell ``generate_narration`` where the cues are without changing its call.

    The offline voice needs the cue word indices to place cue onsets exactly;
    edge-tts does not. Callers (and test doubles) keep the two-argument call.
    """
    token = _cue_hint.set(cue_word_indices)
    try:
        yield
    finally:
        _cue_hint.reset(token)


def engine_tag() -> str | None:
    """Identity of the offline voice when it is the one speaking, else None.

    Part of the section cache key: the edge-tts voice is already keyed by
    ``cli._content_hash``, and this covers Kokoro, whether chosen with
    ``tts.engine`` or switched to after edge-tts failed. It names the engine
    that actually speaks: after Kokoro proved unavailable it is None, so edge-tts
    clips are never mixed up with Kokoro clips.
    """
    if _kokoro_unavailable:
        return None
    if _TTS_CFG.get("engine") == "kokoro" or _edge_unreachable:
        k = _TTS_CFG.get("kokoro") or {}
        return f"kokoro:{k.get('voice')}:{k.get('speed')}"
    return None


def generate_narration(
    text: str,
    output_path: str,
    voice: str | None = None,
    cue_word_indices: list[int] | None = None,
) -> tuple[str, list[WordTimestamp]]:
    """Generate narration with the configured engine.

    ``tts.engine`` picks edge-tts or the offline Kokoro voice. Two fallbacks,
    each used at most once per run and never chained into a loop:

    * engine kokoro, but kokoro-onnx is not installed or its model files cannot
      be obtained: one warning with the install command, then edge-tts speaks
      for the rest of the run.
    * engine edge-tts, but edge-tts fails with a network or service error and
      ``tts.fallback_engine`` is kokoro and kokoro-onnx is installed: one line
      is logged and Kokoro speaks for the rest of the run.

    If the engine a fallback switched to fails as well, the error names both.
    ``cue_word_indices`` lets Kokoro place cue onsets exactly; edge-tts ignores
    it (see ``cue_hint``). ``voice`` names an edge-tts voice.
    """
    global _edge_unreachable, _kokoro_unavailable
    if cue_word_indices is None:
        cue_word_indices = _cue_hint.get()
    kokoro_error: Exception | None = None

    if _TTS_CFG.get("engine") == "kokoro" and not _kokoro_unavailable:
        kokoro = _offline_voice()
        try:
            return kokoro.generate_narration(
                text, output_path, cue_word_indices=cue_word_indices
            )
        except kokoro.KokoroNotReady as e:
            kokoro_error = e
            _kokoro_unavailable = True
            logger.warning(
                "[tts] tts.engine is kokoro but the offline voice is not ready "
                "(%s). To install it: pip install -r requirements-voice.txt "
                "(the model files download on first use, which needs the "
                "network once). Narrating with edge-tts for the rest of this "
                "run.",
                e,
            )
    elif _edge_unreachable and not _kokoro_unavailable:
        try:
            return _offline_voice().generate_narration(
                text, output_path, cue_word_indices=cue_word_indices
            )
        except _offline_voice().KokoroNotReady as e:
            # Edge-tts already failed this run; do not go back to it.
            _edge_unreachable = False
            _kokoro_unavailable = True
            raise RuntimeError(
                f"edge-tts could not be reached and the offline voice is not "
                f"ready either: {e}"
            ) from e

    try:
        return _generate_edge(text, output_path, voice)
    except RuntimeError as e:
        if kokoro_error is not None:
            raise RuntimeError(
                f"{e}. The offline voice (Kokoro) was tried first and is not "
                f"ready: {kokoro_error}"
            ) from e
        can_fall_back = (
            not _kokoro_unavailable
            and _TTS_CFG.get("fallback_engine", "kokoro") == "kokoro"
            and _is_service_failure(e)
            and _offline_voice().is_installed()
        )
        if not can_fall_back:
            raise
        logger.warning(
            "[tts] edge-tts is not reachable (%s); using the offline "
            "voice (Kokoro) for the rest of this run.",
            e.__cause__ or e,
        )
        _edge_unreachable = True
    try:
        return _offline_voice().generate_narration(
            text, output_path, cue_word_indices=cue_word_indices
        )
    except _offline_voice().KokoroNotReady as k:
        _edge_unreachable = False
        _kokoro_unavailable = True
        raise RuntimeError(
            f"edge-tts could not be reached and the offline voice is not "
            f"ready either: {k}"
        ) from k


def save_timestamps(timestamps: list[WordTimestamp], json_path: str) -> None:
    """Persist word timestamps to a JSON file next to the audio."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    data = [{"word": t.word, "start": t.start, "end": t.end} for t in timestamps]
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_timestamps(json_path: str) -> list[WordTimestamp]:
    """Load previously saved word timestamps from JSON."""
    with open(json_path, encoding="utf-8") as f:
        data = json.load(f)
    return [WordTimestamp(word=d["word"], start=d["start"], end=d["end"]) for d in data]


def cue_times(
    timestamps: list[WordTimestamp], cue_word_indices: list[int]
) -> list[float]:
    """Given a list of cue word indices (0-based), return the start time of each.

    Example:
        cue_word_indices = [0, 13, 27]
        → [0.113, 4.200, 9.750]   (seconds)

    The animation for interval i plays from cue_times[i] until cue_times[i+1]
    (or until the end of audio for the last interval).
    """
    times = []
    for idx in cue_word_indices:
        if idx < 0 or idx >= len(timestamps):
            raise IndexError(
                f"Cue word index {idx} is out of range "
                f"(narration has {len(timestamps)} words)"
            )
        times.append(timestamps[idx].start)
    return times


# ---------------------------------------------------------------------------
# Audio duration helper
# ---------------------------------------------------------------------------


def get_audio_duration(audio_path: str) -> float:
    """Return duration of an audio file in seconds using ffprobe (always > 0).

    Some containers report no format-level duration ("N/A" or absent),
    which previously crashed via ``float("N/A")``. Parsing is delegated to
    ``utils.safe_probe_duration``; an unreadable duration falls back to the
    0.1s floor (mirroring ``assembler._video_duration``) so callers never
    crash on missing metadata.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                audio_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install FFmpeg to enable audio duration detection:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = None
    dur = safe_probe_duration(data)
    if dur is None:
        logging.getLogger(__name__).warning(
            "[tts] No readable duration for %s — using 0.1s floor", audio_path
        )
        dur = 0.1
    return max(0.1, dur)


# ebur128 decodes the whole section narration to measure momentary loudness.
# That is tens of seconds of audio, so it needs a real budget — but it is NOT a
# whole-video operation, so it stays well under the 300s the muxer/assembler
# give a full concat. Bounded because this runs once per section during Phase 1,
# BEFORE any render: an unbounded hang here stalls the entire run with no output.
_EBUR128_TIMEOUT_SECONDS = 120


def check_audio_not_silent(audio_path: str) -> dict:
    """Return silence report for an audio file using ffmpeg ebur128.

    Returns {"ok": bool, "silent_ratio": float, "duration": float,
             "timed_out": bool}.
    Flags as silent if >80% of momentary loudness measurements are below -60 LUFS.

    On probe timeout the report degrades to ok=True with timed_out=True: this is
    an advisory silence *check*, so a wedged ffmpeg must not be reported as
    "audio is silent" (a false alarm that would discard good narration) nor
    crash Phase 1 before the pipeline has produced anything.
    """
    import re as _re

    cmd = [
        "ffmpeg",
        "-i",
        audio_path,
        "-af",
        "ebur128=peak=true",
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=_EBUR128_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning(
            "[tts] ebur128 silence probe timed out after %ds for %s — "
            "skipping silence check (audio NOT verified)",
            _EBUR128_TIMEOUT_SECONDS,
            audio_path,
        )
        return {
            "ok": True,
            "silent_ratio": 0.0,
            "duration": 0.0,
            "timed_out": True,
        }
    output = result.stderr

    m_values = [float(m) for m in _re.findall(r"M:\s*([-\d.]+)", output)]
    if not m_values:
        return {"ok": True, "silent_ratio": 0.0, "duration": 0.0, "timed_out": False}

    silent = sum(1 for v in m_values if v < -60.0)
    ratio = silent / len(m_values)

    try:
        duration = get_audio_duration(audio_path)
    except Exception as e:
        logger.warning(
            "[tts] Could not probe duration for %s (%s) — reporting 0.0",
            audio_path,
            e,
        )
        duration = 0.0

    return {
        "ok": ratio < 0.8,
        "silent_ratio": ratio,
        "duration": duration,
        "timed_out": False,
    }
