# TTS support — Phase 4
# Generates spoken narration audio from text using edge-tts (Microsoft Neural TTS).
#
# Available voices (set via config.yaml tts.voice):
#   "en-US-AndrewMultilingualNeural" — male, smoother/prosodic technical narration (default)
#   "en-US-GuyNeural"    — male, neutral
#   "en-US-JennyNeural"  — female, warm
#   "en-US-EricNeural"   — male, authoritative

import asyncio
import json
import logging
import os
import subprocess

import edge_tts
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def _load_tts_config() -> dict:
    """Load TTS settings from config.yaml, returning sensible defaults if missing."""
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
# Core TTS function
# ---------------------------------------------------------------------------

async def _generate_async(text: str, output_path: str, voice: str, rate: str) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(output_path)


def generate_narration(
    text: str,
    output_path: str,
    voice: str = None,
) -> str:
    """Generate narration audio from text using edge-tts.

    Returns the path to the generated .mp3 file.
    Falls back to config.yaml settings for voice and speed.
    """
    if voice is None:
        voice = _TTS_CFG.get("voice", _DEFAULT_VOICE)
    rate = _TTS_CFG.get("speed", _DEFAULT_SPEED)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    asyncio.run(_generate_async(text, output_path, voice, rate))
    return output_path


# ---------------------------------------------------------------------------
# Audio duration helper
# ---------------------------------------------------------------------------

def get_audio_duration(audio_path: str) -> float:
    """Return duration of an audio file in seconds using ffprobe.

    Raises RuntimeError with install instructions if ffprobe is not found.
    """
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
