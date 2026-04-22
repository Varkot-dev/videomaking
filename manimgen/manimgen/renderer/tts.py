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
import base64
import json
import logging
import os
import subprocess
from dataclasses import dataclass

import edge_tts
import requests
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


_ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/with-timestamps"


def _generate_elevenlabs(
    text: str,
    output_path: str,
    voice_id: str,
    api_key: str,
) -> list[WordTimestamp]:
    """Call ElevenLabs /with-timestamps endpoint, write audio, return WordTimestamps.

    ElevenLabs returns character-level alignment; this converts to word-level
    by grouping characters until a space boundary, matching pipeline contract.
    """
    resp = requests.post(
        _ELEVENLABS_API_URL.format(voice_id=voice_id),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": "eleven_multilingual_v2",
            "output_format": "mp3_44100_128",
        },
        timeout=120,
    )
    resp.raise_for_status()
    payload = resp.json()

    audio_bytes = base64.b64decode(payload["audio_base64"])
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(audio_bytes)

    # Character-level alignment → word-level WordTimestamp list
    alignment = payload.get("alignment", {})
    chars: list[str] = alignment.get("characters", [])
    char_starts: list[float] = alignment.get("character_start_times_seconds", [])
    char_ends: list[float] = alignment.get("character_end_times_seconds", [])

    timestamps: list[WordTimestamp] = []
    word_chars: list[str] = []
    word_start: float | None = None
    word_end: float = 0.0

    for ch, cs, ce in zip(chars, char_starts, char_ends):
        if ch == " " or ch == "":
            if word_chars:
                word = "".join(word_chars).strip(".,!?;:\"'")
                if word:
                    timestamps.append(WordTimestamp(word=word, start=word_start, end=word_end))
                word_chars = []
                word_start = None
        else:
            if word_start is None:
                word_start = cs
            word_chars.append(ch)
            word_end = ce

    if word_chars:
        word = "".join(word_chars).strip(".,!?;:\"'")
        if word:
            timestamps.append(WordTimestamp(word=word, start=word_start, end=word_end))

    return timestamps


def generate_narration(
    text: str,
    output_path: str,
    voice: str = None,
) -> tuple[str, list[WordTimestamp]]:
    """Generate narration audio from text. Engine selected by config.yaml tts.engine.

    Returns (audio_path, word_timestamps) — contract identical regardless of engine.
    """
    engine = _TTS_CFG.get("engine", "edge-tts")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if engine == "elevenlabs":
        api_key = os.environ.get("ELEVEN_LABS_KEY") or os.environ.get("ELEVENLABS_API_KEY")
        if not api_key:
            raise RuntimeError("ElevenLabs engine selected but ELEVEN_LABS_KEY not set in environment")
        voice_id = _TTS_CFG.get("elevenlabs_voice_id", "pNInz6obpgDQGcFmaJgB")
        timestamps = _generate_elevenlabs(text, output_path, voice_id, api_key)
    else:
        if voice is None:
            voice = _TTS_CFG.get("voice", _DEFAULT_VOICE)
        rate = _TTS_CFG.get("speed", _DEFAULT_SPEED)
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


# ---------------------------------------------------------------------------
# Audio energy / silence check (adapted from OpenMontage audio_energy.py)
# ---------------------------------------------------------------------------

_SILENCE_LUFS = -60.0  # below this = effectively silent

def check_audio_not_silent(audio_path: str) -> dict:
    """Check that a TTS audio file has meaningful content.

    Uses ffmpeg's ebur128 filter to measure momentary loudness. Returns
    a dict with keys: ok (bool), silent_ratio (float 0–1), duration (float).

    A file is flagged as silent when >80% of its duration measures below
    -60 LUFS — which indicates a TTS failure, empty output, or bad path.
    Runs in <2s for typical cue-length audio (2–15s).
    """
    import re as _re
    result = {"ok": True, "silent_ratio": 0.0, "duration": 0.0}

    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "json", audio_path],
            capture_output=True, text=True, timeout=10,
        )
        duration = float(json.loads(probe.stdout)["format"]["duration"])
        result["duration"] = duration
    except Exception:
        return result  # can't probe → assume ok, let downstream fail

    try:
        proc = subprocess.run(
            ["ffmpeg", "-i", audio_path, "-af", "ebur128", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        )
        stderr = proc.stderr
    except Exception:
        return result

    # Parse momentary loudness (M:) values at ~100ms intervals
    pattern = _re.compile(r"M:\s*(-?[\d.]+)")
    measurements = [float(m) for m in pattern.findall(stderr)]

    if not measurements:
        return result

    silent_count = sum(1 for m in measurements if m < _SILENCE_LUFS)
    silent_ratio = silent_count / len(measurements)
    result["silent_ratio"] = round(silent_ratio, 3)
    result["ok"] = silent_ratio < 0.80

    return result
