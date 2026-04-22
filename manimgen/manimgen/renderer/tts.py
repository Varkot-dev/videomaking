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
import json
import logging
import os
import subprocess
from dataclasses import dataclass

import edge_tts
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_tts_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "..", "config.yaml")
    try:
        with open(config_path) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("tts", {})
    except Exception:
        return {}


_TTS_CFG = _load_tts_config()

_DEFAULT_VOICE = "en-US-AndrewMultilingualNeural"
_DEFAULT_SPEED = "+5%"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class WordTimestamp:
    word: str
    start: float   # seconds from audio start
    end: float     # seconds from audio start


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
    communicate = edge_tts.Communicate(text, voice, rate=rate, boundary="WordBoundary")

    audio_chunks: list[bytes] = []
    word_timestamps: list[WordTimestamp] = []

    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_chunks.append(chunk["data"])
        elif chunk["type"] == "WordBoundary":
            # edge-tts reports offsets in 100-nanosecond units
            start_sec = chunk["offset"] / 10_000_000
            duration_sec = chunk["duration"] / 10_000_000
            word_timestamps.append(WordTimestamp(
                word=chunk["text"],
                start=start_sec,
                end=start_sec + duration_sec,
            ))

    with open(output_path, "wb") as f:
        f.write(b"".join(audio_chunks))

    return word_timestamps


def generate_narration(
    text: str,
    output_path: str,
    voice: str = None,
) -> tuple[str, list[WordTimestamp]]:
    """Generate narration audio from text using edge-tts.

    Returns (audio_path, word_timestamps).

    word_timestamps is a list of WordTimestamp(word, start, end) in order,
    where start/end are seconds from the beginning of the audio file.
    These are used to cue animations: when word[i] starts speaking,
    the animation associated with that cue point begins.
    """
    if voice is None:
        voice = _TTS_CFG.get("voice", _DEFAULT_VOICE)
    rate = _TTS_CFG.get("speed", _DEFAULT_SPEED)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    timestamps = asyncio.run(_generate_async(text, output_path, voice, rate))
    return output_path, timestamps


def save_timestamps(timestamps: list[WordTimestamp], json_path: str) -> None:
    """Persist word timestamps to a JSON file next to the audio."""
    os.makedirs(os.path.dirname(json_path), exist_ok=True)
    data = [{"word": t.word, "start": t.start, "end": t.end} for t in timestamps]
    with open(json_path, "w") as f:
        json.dump(data, f, indent=2)


def load_timestamps(json_path: str) -> list[WordTimestamp]:
    """Load previously saved word timestamps from JSON."""
    with open(json_path) as f:
        data = json.load(f)
    return [WordTimestamp(word=d["word"], start=d["start"], end=d["end"]) for d in data]


def cue_times(timestamps: list[WordTimestamp], cue_word_indices: list[int]) -> list[float]:
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
    """Return duration of an audio file in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                audio_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install FFmpeg to enable audio duration detection:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )

    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def check_audio_not_silent(audio_path: str) -> dict:
    """Return silence report for an audio file using ffmpeg ebur128.

    Returns {"ok": bool, "silent_ratio": float, "duration": float}.
    Flags as silent if >80% of momentary loudness measurements are below -60 LUFS.
    """
    import re as _re
    cmd = [
        "ffmpeg", "-i", audio_path,
        "-af", "ebur128=peak=true",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    m_values = [float(m) for m in _re.findall(r"M:\s*([-\d.]+)", output)]
    if not m_values:
        return {"ok": True, "silent_ratio": 0.0, "duration": 0.0}

    silent = sum(1 for v in m_values if v < -60.0)
    ratio = silent / len(m_values)

    try:
        duration = get_audio_duration(audio_path)
    except Exception:
        duration = 0.0

    return {"ok": ratio < 0.8, "silent_ratio": ratio, "duration": duration}
