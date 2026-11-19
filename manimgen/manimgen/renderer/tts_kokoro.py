# Offline narration engine: Kokoro (82M parameters, Apache-2.0) through kokoro-onnx.
#
# Produces the same two things the edge-tts path produces, so everything after
# narration (segmenter, cue_parser re-derivation, audio_slicer, muxer) runs
# unchanged:
#   - the narration audio at output_path (an .mp3, mono, 48 kHz), and
#   - a list of WordTimestamp(word, start, end), one per ``str.split()`` token
#     of the narration, in seconds from the start of the file.
#
# kokoro-onnx reports no word timestamps, so timings are built, not measured:
#
#   cue level     Each cue's words are synthesized on their own and the clips
#                 are joined with a short silence. A cue onset is therefore the
#                 exact sample where that cue's audio starts. The slicer cuts at
#                 these onsets, so slices never contain another cue's speech.
#   phrase level  Inside a cue the text is split at punctuation and each phrase
#                 is synthesized on its own, so phrase boundaries are exact too.
#   word level    Inside a phrase, each word gets a share of the phrase's
#                 (silence-trimmed) duration proportional to its phoneme count.
#                 This is an estimate. The first word's start and the last
#                 word's end are exact, interior boundaries are not.
#
# Sync contracts kept (see CLAUDE.md "A/V sync"): cue 0 is sliced from 0.0 and
# the file starts with a short lead-in of silence, so word 0 starts after 0.0;
# the last word of every cue ends inside that cue's audio, before the next cue
# onset; the file ends with a short tail so the last word.end is below the file
# duration. The slicer re-encodes to AAC at 48 kHz as before.

from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
import sys
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests

from manimgen import config
from manimgen.renderer.tts import WordTimestamp

logger = logging.getLogger(__name__)

SAMPLE_RATE = 24000  # kokoro's native rate

ENV_MODEL_DIR = "MANIMGEN_KOKORO_DIR"

_RELEASE_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
)
# File name -> exact size in bytes of the published release asset. The size is
# the integrity check: a truncated download never gets renamed into place.
MODEL_FILES: dict[str, int] = {
    "kokoro-v1.0.int8.onnx": 92_361_271,
    "voices-v1.0.bin": 28_214_398,
}
_MODEL_FILE, _VOICES_FILE = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"

# (connect, read) seconds for each HTTP call. The read timeout applies to each
# chunk, not the whole file, so a slow but moving download is not cut off.
_HTTP_TIMEOUT = (15, 60)
_FFMPEG_TIMEOUT_SECONDS = 120

# Silence, in seconds, added around and between spoken parts.
LEAD_IN = 0.10  # before word 0 (cue 0 is sliced from 0.0, so this is kept)
TAIL = 0.20  # after the last word
_PAUSE_SENTENCE = 0.28  # after . ! ? ... (inside a cue)
_PAUSE_CLAUSE = 0.18  # after ; : and a dash
_PAUSE_COMMA = 0.12  # after ,
_PAUSE_NONE = 0.05
_CUE_GAP_MIN = 0.20  # between cues, whatever the punctuation

_MIN_CLAUSE_WORDS = 3  # a comma phrase shorter than this is merged into its neighbour


class KokoroUnavailable(RuntimeError):
    """The offline voice cannot be used."""


class KokoroNotReady(KokoroUnavailable):
    """Set-up is missing: the package is not installed or the model files
    cannot be obtained. ``tts.generate_narration`` answers this one by
    narrating with edge-tts instead; other failures are real errors."""


# ---------------------------------------------------------------------------
# Availability and model files
# ---------------------------------------------------------------------------


def is_installed() -> bool:
    """True when the kokoro-onnx package can be imported (not imported here)."""
    try:
        return importlib.util.find_spec("kokoro_onnx") is not None
    except (ImportError, ValueError):
        return False


def default_cache_dir(
    platform: str | None = None,
    env: dict | None = None,
    home: str | Path | None = None,
) -> Path:
    """Per-user cache folder for the model files, by operating system.

    Windows: %LOCALAPPDATA%\\manimgen\\kokoro. macOS: ~/Library/Caches/manimgen/kokoro.
    Elsewhere: $XDG_CACHE_HOME/manimgen/kokoro, else ~/.cache/manimgen/kokoro.
    """
    platform = sys.platform if platform is None else platform
    env = os.environ if env is None else env
    home_dir = Path(home) if home is not None else Path.home()
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or str(home_dir / "AppData" / "Local")
    elif platform == "darwin":
        base = str(home_dir / "Library" / "Caches")
    else:
        base = env.get("XDG_CACHE_HOME") or str(home_dir / ".cache")
    return Path(base) / "manimgen" / "kokoro"


def kokoro_settings() -> dict:
    """The validated ``tts.kokoro`` block."""
    return dict(config.section("tts")["kokoro"])


def model_dir(settings: dict | None = None) -> Path:
    """Where the model files live: env var, then tts.kokoro.model_dir, then the cache."""
    env_dir = os.environ.get(ENV_MODEL_DIR, "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    settings = kokoro_settings() if settings is None else settings
    configured = settings.get("model_dir")
    if configured:
        return Path(configured).expanduser()
    return default_cache_dir()


def _file_ok(path: Path, size: int) -> bool:
    try:
        return path.is_file() and path.stat().st_size == size
    except OSError:
        return False


def models_cached(directory: Path | None = None) -> bool:
    """True when both model files are present with their published sizes."""
    directory = model_dir() if directory is None else Path(directory)
    return all(_file_ok(directory / n, s) for n, s in MODEL_FILES.items())


def _download_one(url: str, dest: Path, size: int, proxy: str | None) -> None:
    """Download ``url`` to ``dest`` via a temp file, size-checked, then renamed."""
    part = dest.with_name(f"{dest.name}.part.{os.getpid()}")
    proxies = {"https": proxy, "http": proxy} if proxy else None
    try:
        with requests.get(
            url, stream=True, timeout=_HTTP_TIMEOUT, proxies=proxies
        ) as resp:
            resp.raise_for_status()
            written = 0
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
        if written != size:
            raise KokoroNotReady(
                f"downloaded {dest.name} is {written} bytes, expected {size}"
            )
        os.replace(part, dest)  # atomic: a partial file never has the real name
    finally:
        try:
            part.unlink()
        except FileNotFoundError:
            pass


def ensure_models(directory: Path | None = None) -> tuple[Path, Path]:
    """Return (model_path, voices_path), downloading missing files first.

    Raises KokoroUnavailable with the fix when the download cannot complete.
    """
    directory = model_dir() if directory is None else Path(directory)
    missing = [(n, s) for n, s in MODEL_FILES.items() if not _file_ok(directory / n, s)]
    if missing:
        proxy = config.section("tts").get("proxy") or None
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for name, size in missing:
                logger.info(
                    "[tts] downloading the offline voice file %s (%.0f MB) to %s",
                    name,
                    size / 1e6,
                    directory,
                )
                _download_one(_RELEASE_URL + name, directory / name, size, proxy)
        except KokoroUnavailable:
            raise
        except (requests.RequestException, OSError) as e:
            raise KokoroNotReady(
                f"could not download the offline voice files to {directory}: {e}. "
                f"Check the network (behind a proxy, set HTTPS_PROXY or tts.proxy), "
                f"or download {', '.join(n for n, _ in missing)} yourself from "
                f"{_RELEASE_URL} into that folder."
            ) from e
    return directory / _MODEL_FILE, directory / _VOICES_FILE


# ---------------------------------------------------------------------------
# Splitting text
# ---------------------------------------------------------------------------

_TRAILING_CLOSERS = "\"')]}”’"
_SENTENCE_END = (".", "!", "?", "…")
_CLAUSE_END = (";", ":", "—", "–")


def _end_mark(word: str) -> str:
    """The punctuation class a word ends with: 'sentence', 'clause', 'comma' or ''."""
    w = word.rstrip(_TRAILING_CLOSERS)
    if w.endswith(_SENTENCE_END):
        return "sentence"
    if w.endswith(_CLAUSE_END):
        return "clause"
    if w.endswith(","):
        return "comma"
    return ""


def _pause_after(word: str) -> float:
    return {
        "sentence": _PAUSE_SENTENCE,
        "clause": _PAUSE_CLAUSE,
        "comma": _PAUSE_COMMA,
    }.get(_end_mark(word), _PAUSE_NONE)


def _speakable(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def split_phrases(words: list[str]) -> list[list[str]]:
    """Split one cue's words into phrases at punctuation.

    Sentence ends always close a phrase. A comma, semicolon, colon or dash closes
    one only when at least three words are waiting, so the model is never fed
    two-word scraps (which sound clipped). A trailing scrap is merged into the
    phrase before it. Tokens with nothing to say aloud (a lone dash) stay with
    the previous word.
    """
    phrases: list[list[str]] = []
    current: list[str] = []
    for word in words:
        current.append(word)
        mark = _end_mark(word)
        if mark == "sentence" or (
            mark in ("clause", "comma") and len(current) >= _MIN_CLAUSE_WORDS
        ):
            phrases.append(current)
            current = []
    if current:
        if phrases and len(current) < _MIN_CLAUSE_WORDS:
            phrases[-1].extend(current)
        else:
            phrases.append(current)
    return phrases


def split_cues(words: list[str], cue_word_indices: list[int] | None) -> list[list[str]]:
    """Cut the narration's words at the cue word indices (one list per cue).

    Indices are sorted, de-duplicated and clamped into range; the first cue always
    starts at word 0, matching ``cue_parser.parse_cues``.
    """
    n = len(words)
    starts = sorted({0, *[i for i in (cue_word_indices or []) if 0 < i < n]})
    bounds = [*starts, n]
    return [words[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]


# ---------------------------------------------------------------------------
# Timing estimation
# ---------------------------------------------------------------------------

_NOT_SOUND = re.compile(r"[\s.,;:!?\"'()\[\]{}‘’“”—–…ˈˌ-]")


def phoneme_weight(phonemes: str) -> float:
    """How much speaking time a word's phonemes take (never below 1)."""
    return float(max(1, len(_NOT_SOUND.sub("", phonemes))))


def char_weights(words: list[str]) -> list[float]:
    """Fallback weights when no phonemizer is available: letters and digits."""
    return [float(max(1, sum(ch.isalnum() for ch in w))) for w in words]


def estimate_word_spans(
    words: list[str], weights: list[float], start: float, duration: float
) -> list[WordTimestamp]:
    """Spread ``duration`` seconds over ``words`` in proportion to ``weights``.

    Words are contiguous: each word ends where the next begins, the first starts at
    ``start`` and the last ends at ``start + duration`` exactly.
    """
    if not words:
        return []
    total = float(sum(weights)) or float(len(words))
    out: list[WordTimestamp] = []
    acc = 0.0
    for i, (word, w) in enumerate(zip(words, weights)):
        a = start + duration * acc / total
        acc += w
        b = start + duration * acc / total if i < len(words) - 1 else start + duration
        out.append(WordTimestamp(word=word, start=a, end=b))
    return out


def trim_silence(
    samples: np.ndarray, sample_rate: int = SAMPLE_RATE, floor: float = 0.004
) -> np.ndarray:
    """Cut leading and trailing near-silence, the same rule at both ends.

    A frame counts as silent when its peak is below ``floor`` of full scale or
    below 2% of the clip's own peak, whichever is higher. A 10 ms margin is kept
    so soft consonants at the edges are not clipped.
    """
    samples = np.asarray(samples, dtype=np.float32).ravel()
    if samples.size == 0:
        return samples
    frame = max(1, int(0.005 * sample_rate))
    n = samples.size // frame
    if n == 0:
        return samples
    peaks = np.abs(samples[: n * frame]).reshape(n, frame).max(axis=1)
    limit = max(floor, 0.02 * float(peaks.max()))
    loud = np.flatnonzero(peaks >= limit)
    if loud.size == 0:
        return samples[: int(0.05 * sample_rate)]
    margin = int(0.010 * sample_rate)
    begin = max(0, int(loud[0]) * frame - margin)
    end = min(samples.size, (int(loud[-1]) + 1) * frame + margin)
    return samples[begin:end]


# ---------------------------------------------------------------------------
# Building the narration timeline
# ---------------------------------------------------------------------------


@dataclass
class Timeline:
    samples: np.ndarray  # float32 mono at SAMPLE_RATE
    timestamps: list[WordTimestamp]
    cue_onsets: list[float]  # exact start (seconds) of each cue's speech
    cue_audio_starts: list[float]  # first sample of each cue's clip, gap excluded


def _silence(seconds: float) -> np.ndarray:
    return np.zeros(int(round(seconds * SAMPLE_RATE)), dtype=np.float32)


def build_timeline(
    words: list[str], cue_word_indices: list[int] | None, synth
) -> Timeline:
    """Synthesize the narration cue by cue and phrase by phrase.

    ``synth`` needs two methods: ``synth.speak(text) -> float32 samples at
    SAMPLE_RATE`` and ``synth.weights(words) -> list[float]``. A cue onset is the
    cumulative length of everything before it, so it is exact.
    """
    parts: list[np.ndarray] = [_silence(LEAD_IN)]
    cursor = LEAD_IN
    stamps: list[WordTimestamp] = []
    onsets: list[float] = []
    cues = split_cues(words, cue_word_indices)
    for ci, cue_words in enumerate(cues):
        onsets.append(cursor)
        phrases = split_phrases(cue_words)
        for pi, phrase in enumerate(phrases):
            text = " ".join(phrase)
            if _speakable(text):
                clip = trim_silence(synth.speak(text))
                weights = synth.weights(phrase)
            else:
                clip = _silence(0.1)
                weights = char_weights(phrase)
            duration = len(clip) / SAMPLE_RATE
            stamps.extend(estimate_word_spans(phrase, weights, cursor, duration))
            parts.append(clip)
            cursor += duration
            last_phrase = pi == len(phrases) - 1
            if last_phrase and ci == len(cues) - 1:
                continue  # the tail is added once, below
            pause = _pause_after(phrase[-1])
            if last_phrase:
                pause = max(pause, _CUE_GAP_MIN)
            parts.append(_silence(pause))
            cursor += len(parts[-1]) / SAMPLE_RATE
    parts.append(_silence(TAIL))
    samples = np.concatenate(parts).astype(np.float32)
    return Timeline(samples, stamps, onsets, onsets)


# ---------------------------------------------------------------------------
# The engine (wraps kokoro_onnx.Kokoro)
# ---------------------------------------------------------------------------

_engine_lock = threading.Lock()
_engines: dict[tuple[str, str], "KokoroSynth"] = {}


class KokoroSynth:
    """Speaks text and counts phonemes with one loaded kokoro-onnx model."""

    def __init__(self, kokoro, voice: str, speed: float, lang: str = "en-us"):
        self._k = kokoro
        self.voice = voice
        self.speed = speed
        self.lang = lang

    def speak(self, text: str) -> np.ndarray:
        samples, rate = self._k.create(
            text, voice=self.voice, speed=self.speed, lang=self.lang
        )
        if rate != SAMPLE_RATE:
            raise KokoroUnavailable(
                f"kokoro returned {rate} Hz audio, expected {SAMPLE_RATE}"
            )
        return np.asarray(samples, dtype=np.float32).ravel()

    def weights(self, words: list[str]) -> list[float]:
        """Per-word phoneme counts. Phrase-level phonemes when the word count
        agrees (keeps cross-word effects), word by word otherwise (a number such
        as "25" is one word but several spoken ones). Letters if that fails."""
        try:
            tokenizer = self._k.tokenizer
            joined = tokenizer.phonemize(" ".join(words), self.lang).split()
            if len(joined) == len(words):
                return [phoneme_weight(p) for p in joined]
            return [phoneme_weight(tokenizer.phonemize(w, self.lang)) for w in words]
        except Exception as e:  # phonemizer trouble must not fail the narration
            logger.warning("[tts] phoneme count failed (%s); using letter counts", e)
            return char_weights(words)


def load_synth(settings: dict | None = None) -> KokoroSynth:
    """Load (once per model, voice and speed) the offline voice."""
    settings = kokoro_settings() if settings is None else settings
    if not is_installed():
        raise KokoroNotReady(
            "the offline voice needs kokoro-onnx: pip install -r requirements-voice.txt"
        )
    model_path, voices_path = ensure_models(model_dir(settings))
    key = (str(model_path), str(voices_path))
    with _engine_lock:
        synth = _engines.get(key)
        if synth is None:
            from kokoro_onnx import Kokoro

            kokoro = Kokoro(str(model_path), str(voices_path))
            synth = KokoroSynth(kokoro, settings["voice"], settings["speed"])
            _engines[key] = synth
        voices = synth._k.get_voices()
        if settings["voice"] not in voices:
            raise KokoroUnavailable(
                f"kokoro voice {settings['voice']!r} not found; available: "
                f"{', '.join(voices)}"
            )
        synth.voice, synth.speed = settings["voice"], settings["speed"]
        return synth


# ---------------------------------------------------------------------------
# Writing the audio file
# ---------------------------------------------------------------------------


def write_mp3(samples: np.ndarray, output_path: str) -> None:
    """Write ``samples`` (24 kHz float mono) as a 48 kHz mono mp3 via ffmpeg."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    wav = out.with_name(f"{out.stem}.{os.getpid()}.tmp.wav")
    tmp_mp3 = out.with_name(f"{out.stem}.{os.getpid()}.tmp.mp3")
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    try:
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(pcm.tobytes())
        cmd = [
            "ffmpeg", "-y", "-loglevel", "error", "-i", str(wav),
            "-ar", "48000", "-ac", "1", "-c:a", "libmp3lame", "-q:a", "2",
            str(tmp_mp3),
        ]  # fmt: skip
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_FFMPEG_TIMEOUT_SECONDS,
            )
        except FileNotFoundError:
            raise KokoroUnavailable(
                "ffmpeg not found. Install FFmpeg (macOS: brew install ffmpeg, "
                "Ubuntu: sudo apt install ffmpeg, Windows: https://ffmpeg.org/download.html)"
            ) from None
        except subprocess.TimeoutExpired:
            raise KokoroUnavailable(
                f"ffmpeg timed out after {_FFMPEG_TIMEOUT_SECONDS}s encoding {output_path}"
            ) from None
        if result.returncode != 0 or not tmp_mp3.exists():
            raise KokoroUnavailable(
                f"ffmpeg could not encode the narration:\n{result.stderr}"
            )
        os.replace(tmp_mp3, out)
    finally:
        for p in (wav, tmp_mp3):
            try:
                p.unlink()
            except FileNotFoundError:
                pass


def generate_narration(
    text: str,
    output_path: str,
    cue_word_indices: list[int] | None = None,
    synth=None,
) -> tuple[str, list[WordTimestamp]]:
    """Offline counterpart of ``tts.generate_narration``.

    Returns (output_path, word_timestamps) with one timestamp per
    ``text.split()`` token. ``cue_word_indices`` are indices into that same split
    (what ``cue_parser.parse_cues`` returns); without them the narration is one
    cue. ``synth`` is for tests; by default the Kokoro model is loaded.
    """
    words = text.split()
    if synth is None and words:
        synth = load_synth()
    if not words:
        write_mp3(_silence(0.3), output_path)
        return output_path, []
    timeline = build_timeline(words, cue_word_indices, synth)
    write_mp3(timeline.samples, output_path)
    return output_path, timeline.timestamps
