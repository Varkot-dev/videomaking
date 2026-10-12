#!/usr/bin/env python3
"""TTS smoke test: does real narration (edge-tts) work from this machine?

Run it on any machine with internet access (Windows, macOS, Linux):

    python scripts/tts_smoke.py              # temp folder, deleted afterwards
    python scripts/tts_smoke.py --out DIR    # keep audio, timestamps and report
    python scripts/tts_smoke.py --no-survey  # skip the tokenization survey

It never calls an LLM. It does call Microsoft's online speech service through
the pipeline's own narration function (manimgen.renderer.tts.generate_narration,
the one cli._run_tts_for_section uses), so it honours tts.voice, tts.speed and
tts.proxy from config.yaml, and HTTPS_PROXY. The unit tests fake that service;
this script is the only thing that checks the real one.

Checks on a three sentence narration with two [CUE] markers (it contains a
contraction, a number and a hyphenated word):

  a. ffprobe on PATH
  b. real narration: a non-empty audio file with an audio stream
  c. audio duration plausible for the word count (bounds below)
  d. a word timestamp for every spoken word
  e. timestamps monotonic and inside the audio duration
  f. the cue machinery (planner.cue_parser + planner.segmenter) maps every
     [CUE] to the right spoken word and the segments cover the audio

Then a tokenization survey: the sample sentences from
tests/test_cue_tokenization.py are narrated for real and the actual
WordBoundary tokens are printed next to str.split(). That test fakes edge-tts
and ASSUMES one boundary per whitespace word ("well-known" as one token); the
survey shows whether that assumption is true. Survey findings are WARN at most.

Each step prints PASS / FAIL / WARN / SKIP. The exit code is 0 only when no
FAIL-level check failed. A network failure is a single clear FAIL with the
error text and a hint.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from importlib import metadata

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

PASS = "PASS"
FAIL = "FAIL"
WARN = "WARN"
SKIP = "SKIP"

# The narration. Contains a contraction (Don't), a number (16) and a hyphenated
# word (well-known). Two [CUE] markers give three segments.
NARRATION_WITH_CUES = (
    "Don't forget that this array holds 16 numbers. [CUE] "
    "Binary search is a well-known trick that halves the search space every time. "
    "[CUE] That is why it is so fast."
)

# Copies of NARRATION_SAMPLES in tests/test_cue_tokenization.py (a unit test
# keeps the two lists identical). The survey narrates each one for real.
SURVEY_SAMPLES = [
    ("simple sentence", "Binary search cuts the problem in half every time."),
    (
        "contraction",
        "It's a classic algorithm that you'll use throughout your career.",
    ),
    (
        "numbers and symbols",
        "The array has 16 elements so we need at most 4 comparisons.",
    ),
    (
        "multi-sentence with cue context",
        "Watch the array carefully. The middle element is either your target, "
        "or it tells you which half to throw away entirely.",
    ),
    (
        "hyphenated word",
        "This is a well-known technique used in computer science.",
    ),
]

# Duration bounds. Narration runs at roughly 2.5 words per second (150 wpm at
# the default +5% speed). The bounds are deliberately wide, so only a broken
# result (truncated, silent padding, wrong file) trips them:
#   minimum = words / MAX_WORDS_PER_SEC  (nobody speaks faster than this)
#   maximum = words / MIN_WORDS_PER_SEC + SLACK_SECONDS  (slack = lead-in/tail)
MAX_WORDS_PER_SEC = 5.0
MIN_WORDS_PER_SEC = 1.2
SLACK_SECONDS = 2.0
# Last word may end this far past the container duration (mp3 frame rounding).
END_TOLERANCE_SECONDS = 0.25
# Segments must tile the audio: each one ends at its last word's end, so the
# next one may start a little later (the pause between words, at most
# SEGMENT_MAX_GAP_SECONDS) but never earlier than SEGMENT_OVERLAP_SECONDS, and
# the last one must end at the audio duration within SEGMENT_END_TOLERANCE.
SEGMENT_MAX_GAP_SECONDS = 1.5
SEGMENT_OVERLAP_SECONDS = 0.1
SEGMENT_END_TOLERANCE_SECONDS = 0.05

_NON_WORD = re.compile(r"[^0-9a-z]+")


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    hint: str = ""


# ---------------------------------------------------------------------------
# Pure helpers (unit tested)
# ---------------------------------------------------------------------------


def word_key(token: str) -> str:
    """Lowercase alphanumeric content of a token (punctuation dropped)."""
    return _NON_WORD.sub("", token.lower())


def duration_bounds(word_count: int) -> tuple[float, float]:
    """(min, max) plausible audio seconds for ``word_count`` spoken words."""
    n = max(word_count, 1)
    return n / MAX_WORDS_PER_SEC, n / MIN_WORDS_PER_SEC + SLACK_SECONDS


def evaluate_duration(duration: float | None, word_count: int) -> Check:
    lo, hi = duration_bounds(word_count)
    bounds = f"expected {lo:.1f}-{hi:.1f}s for {word_count} words"
    if duration is None:
        return Check(
            "duration plausible",
            FAIL,
            "ffprobe could not read a duration",
            "The audio file is probably truncated or not an audio file.",
        )
    detail = f"{duration:.2f}s ({bounds})"
    if duration < lo or duration > hi:
        return Check(
            "duration plausible",
            FAIL,
            detail,
            "Audio length does not match the text: truncated, or padded with silence.",
        )
    return Check("duration plausible", PASS, detail)


def evaluate_word_coverage(spoken_words: list[str], boundary_words: list[str]) -> Check:
    """Is there a timestamp for every spoken word?

    ``spoken_words`` is the narration split on whitespace. edge-tts may emit
    more tokens than that (hyphens, contractions) but never fewer without
    losing words, so fewer boundaries than words is a FAIL. A different count,
    or different content, is a WARN: the cue aligner is built to absorb it.
    """
    n_spoken, n_bound = len(spoken_words), len(boundary_words)
    if n_bound == 0:
        return Check(
            "word timestamps",
            FAIL,
            f"0 boundaries for {n_spoken} words",
            "The service returned audio but no WordBoundary events; cues cannot work.",
        )
    detail = f"{n_bound} boundaries for {n_spoken} words"
    if n_bound < n_spoken:
        return Check(
            "word timestamps",
            FAIL,
            detail + " (words are missing timestamps)",
            "Compare the boundary list printed above with the narration text.",
        )
    spoken_key = "".join(word_key(w) for w in spoken_words)
    bound_key = "".join(word_key(w) for w in boundary_words)
    if bound_key != spoken_key:
        return Check(
            "word timestamps",
            WARN,
            detail + " (boundary text differs from the narration text)",
            "The service may be normalising text (numbers, symbols). The cue "
            "aligner matches on letters and digits only; check the boundary list.",
        )
    if n_bound != n_spoken:
        return Check(
            "word timestamps",
            WARN,
            detail + " (token counts differ, content matches)",
            "Expected: cue_parser.align_cue_indices re-derives the cue indices.",
        )
    return Check("word timestamps", PASS, detail)


def evaluate_monotonic(
    spans: list[tuple[float, float]], audio_duration: float | None
) -> Check:
    """Starts non-decreasing, end >= start, all inside the audio duration."""
    if not spans:
        return Check("timestamps monotonic", FAIL, "no timestamps to check")
    prev_start = 0.0
    for i, (start, end) in enumerate(spans):
        if start < 0:
            return Check(
                "timestamps monotonic", FAIL, f"word {i} starts at {start:.3f}s"
            )
        if end < start:
            return Check(
                "timestamps monotonic",
                FAIL,
                f"word {i} ends ({end:.3f}s) before it starts ({start:.3f}s)",
            )
        if start < prev_start:
            return Check(
                "timestamps monotonic",
                FAIL,
                f"word {i} starts at {start:.3f}s, before word {i - 1} "
                f"({prev_start:.3f}s)",
            )
        prev_start = start
    last_end = max(end for _, end in spans)
    if audio_duration is not None and last_end > audio_duration + END_TOLERANCE_SECONDS:
        return Check(
            "timestamps monotonic",
            FAIL,
            f"last word ends at {last_end:.2f}s, past the audio "
            f"({audio_duration:.2f}s + {END_TOLERANCE_SECONDS}s tolerance)",
            "Timestamps and audio disagree; animations would outrun the narration.",
        )
    detail = f"{len(spans)} words in order, last ends {last_end:.2f}s"
    if audio_duration is not None:
        detail += f" of {audio_duration:.2f}s"
    return Check("timestamps monotonic", PASS, detail)


def evaluate_cues(
    spoken_words: list[str],
    split_cue_indices: list[int],
    aligned_indices: list[int],
    boundary_words: list[str],
    segments: list[tuple[float, float]],
    audio_duration: float,
) -> Check:
    """Does every [CUE] land on the right spoken word, and do segments tile the audio?

    ``segments`` is a list of (start_time, duration). The word the aligned cue
    index points at must be the word that follows the marker in the text.
    """
    if len(aligned_indices) != len(split_cue_indices):
        return Check(
            "cue mapping",
            FAIL,
            f"{len(split_cue_indices)} cues became {len(aligned_indices)}",
        )
    if len(segments) != len(split_cue_indices):
        return Check(
            "cue mapping",
            FAIL,
            f"{len(split_cue_indices)} cues produced {len(segments)} segments",
        )
    notes = []
    for cue, idx in zip(split_cue_indices, aligned_indices):
        if idx >= len(boundary_words) or cue >= len(spoken_words):
            return Check("cue mapping", FAIL, f"cue index {idx} is out of range")
        want = word_key(spoken_words[cue])
        got = word_key(boundary_words[idx])
        if want != got:
            return Check(
                "cue mapping",
                FAIL,
                f"cue at text word {cue} ({spoken_words[cue]!r}) resolved to "
                f"boundary {idx} ({boundary_words[idx]!r})",
                "The cue would fire on the wrong word: audio and animation desync.",
            )
        notes.append(f"{spoken_words[cue]!r}->{idx}")
    if any(d <= 0 for _, d in segments):
        return Check("cue mapping", FAIL, "a segment has zero or negative duration")
    # Segment 0 is cut from 0.0 (pre-speech silence kept); the others from onset.
    begins = [0.0 if i == 0 else st for i, (st, _) in enumerate(segments)]
    ends = [b + d for b, (_, d) in zip(begins, segments)]
    for i in range(len(segments) - 1):
        nxt = segments[i + 1][0]
        if nxt < begins[i] or ends[i] > nxt + SEGMENT_OVERLAP_SECONDS:
            return Check(
                "cue mapping",
                FAIL,
                f"segment {i} ends at {ends[i]:.2f}s but segment {i + 1} "
                f"starts at {nxt:.2f}s",
                "Segments overlap or run backwards.",
            )
        if nxt - ends[i] > SEGMENT_MAX_GAP_SECONDS:
            return Check(
                "cue mapping",
                FAIL,
                f"{nxt - ends[i]:.2f}s of audio between segment {i} and {i + 1} "
                "belongs to no segment",
            )
    if abs(ends[-1] - audio_duration) > SEGMENT_END_TOLERANCE_SECONDS:
        return Check(
            "cue mapping",
            FAIL,
            f"last segment ends at {ends[-1]:.2f}s, audio is {audio_duration:.2f}s",
        )
    lens = ", ".join(f"{d:.2f}s" for _, d in segments)
    return Check(
        "cue mapping",
        PASS,
        f"{len(segments)} segments ({lens}); cue words {', '.join(notes)}",
    )


def format_tokens(spoken_words: list[str], boundary_words: list[str]) -> str:
    """One survey line: whitespace word count against real boundary count."""
    verdict = "same count" if len(spoken_words) == len(boundary_words) else "DIFFERENT"
    return (
        f"str.split()={len(spoken_words)} boundaries={len(boundary_words)} ({verdict})"
    )


def survey_check(
    label: str, spoken_words: list[str], boundary_words: list[str]
) -> Check:
    """PASS when the real tokenization matches the unit test's fake, else WARN."""
    detail = format_tokens(spoken_words, boundary_words)
    if len(spoken_words) == len(boundary_words):
        return Check(f"tokens: {label}", PASS, detail)
    return Check(
        f"tokens: {label}",
        WARN,
        detail,
        "tests/test_cue_tokenization.py assumes one boundary per whitespace "
        "word; its fake does not match reality here. Update the fake.",
    )


def mask_proxy(url: str | None) -> str:
    """A proxy URL with any user:password removed (safe to print and upload)."""
    if not url:
        return ""
    return re.sub(r"//[^/@]*@", "//***@", url)


def network_hint(error_text: str) -> str:
    """Explain a narration failure; the commonest cause is the network."""
    low = error_text.lower()
    network_words = (
        "connect",
        "ssl",
        "tls",
        "certificate",
        "403",
        "407",
        "proxy",
        "timeout",
        "timed out",
        "websocket",
        "name resolution",
        "getaddrinfo",
        "no word timestamps",
        "no audio",
    )
    if any(w in low for w in network_words):
        return (
            "edge-tts could not reach Microsoft's speech service. This machine is "
            "probably offline or behind a proxy that blocks it. Set HTTPS_PROXY or "
            "tts.proxy in config.yaml, or set tts.enabled: false for silent drafts."
        )
    return "Unexpected narration error; see the text above."


def format_summary(checks: list[Check]) -> str:
    """ASCII summary table (safe for any console encoding)."""
    width = max([len(c.name) for c in checks] + [4])
    lines = ["", "=" * 72, "SUMMARY", "=" * 72]
    for c in checks:
        lines.append(f"  {c.status:<5} {c.name:<{width}}  {c.detail}".rstrip())
    counts = {s: sum(1 for c in checks if c.status == s) for s in (PASS, WARN, FAIL)}
    skipped = sum(1 for c in checks if c.status == SKIP)
    lines.append("-" * 72)
    lines.append(
        f"  {counts[PASS]} passed, {counts[WARN]} warnings, "
        f"{counts[FAIL]} failed, {skipped} skipped"
    )
    verdict = (
        "RESULT: narration works from this machine."
        if exit_code(checks) == 0
        else "RESULT: narration NOT verified, fix the FAIL lines above."
    )
    lines.append(f"  {verdict}")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    """0 only when no check has FAIL status (WARN and SKIP never fail the run)."""
    return 1 if any(c.status == FAIL for c in checks) else 0


def format_check(c: Check) -> str:
    text = f"[{c.status}] {c.name}: {c.detail}"
    if c.hint and c.status in (FAIL, WARN):
        text += f"\n       fix: {c.hint}"
    return text


# ---------------------------------------------------------------------------
# Side-effecting helpers
# ---------------------------------------------------------------------------


def _ensure_project_on_path() -> None:
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)


def edge_tts_version() -> str:
    try:
        return metadata.version("edge-tts")
    except metadata.PackageNotFoundError:
        return "not installed"


def probe_audio(path: str) -> dict:
    """ffprobe an audio file: {has_audio, codec, duration}. Raises on failure."""
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            path,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr.strip()[:300]}")
    data = json.loads(proc.stdout)
    audio = next(
        (s for s in data.get("streams") or [] if s.get("codec_type") == "audio"), None
    )
    duration = None
    for source in ((data.get("format") or {}), (audio or {})):
        try:
            duration = float(source["duration"])
            break
        except (KeyError, TypeError, ValueError):
            continue
    return {
        "has_audio": audio is not None,
        "codec": (audio or {}).get("codec_name"),
        "duration": duration,
    }


def narrate(text: str, path: str):
    """The pipeline's own narration call. Returns the WordTimestamp list."""
    from manimgen.renderer.tts import generate_narration

    _, timestamps = generate_narration(text, path)
    return timestamps


def _one_line(text: str, limit: int = 300) -> str:
    return " ".join(str(text).split())[:limit]


def print_config(emit) -> None:
    from manimgen import config

    cfg = config.section("tts")
    proxy = (
        cfg.get("proxy")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
    )
    emit(f"edge-tts version: {edge_tts_version()}")
    emit(f"voice: {cfg.get('voice', 'en-US-AndrewMultilingualNeural')}")
    emit(f"speed: {cfg.get('speed', '+5%')}")
    emit(f"proxy: {mask_proxy(proxy) or 'none'}")
    emit(f"python: {sys.version.split()[0]} on {sys.platform}")


def run_main_checks(out_dir: str, emit) -> list[Check]:
    from manimgen.planner.cue_parser import align_cue_indices, parse_cues
    from manimgen.planner.segmenter import compute_segments

    checks: list[Check] = []

    if not (shutil.which("ffprobe") and shutil.which("ffmpeg")):
        checks.append(
            Check(
                "ffprobe",
                FAIL,
                "ffmpeg/ffprobe not on PATH",
                "Install FFmpeg (Windows: unzip gyan.dev essentials, add bin to PATH).",
            )
        )
        return checks
    checks.append(Check("ffprobe", PASS, shutil.which("ffprobe") or ""))

    clean, split_cues = parse_cues(NARRATION_WITH_CUES)
    spoken = clean.split()
    emit(f"narration ({len(spoken)} words, cues at word indices {split_cues}):")
    emit(f"  {clean}")

    audio_path = os.path.join(out_dir, "narration.mp3")
    try:
        timestamps = narrate(clean, audio_path)
    except Exception as e:  # network, proxy, service refusal
        text = _one_line(e)
        emit(f"narration error: {text}")
        checks.append(Check("narration", FAIL, text, network_hint(text)))
        return checks

    size = os.path.getsize(audio_path) if os.path.exists(audio_path) else 0
    if size <= 0:
        checks.append(Check("narration", FAIL, "audio file missing or empty"))
        return checks
    try:
        info = probe_audio(audio_path)
    except Exception as e:
        checks.append(
            Check(
                "narration", FAIL, f"ffprobe could not read the audio: {_one_line(e)}"
            )
        )
        return checks
    if not info["has_audio"]:
        checks.append(Check("narration", FAIL, f"{size} bytes but no audio stream"))
        return checks
    checks.append(Check("narration", PASS, f"{size} bytes, codec {info['codec']}"))

    duration = info["duration"]
    boundary_words = [t.word for t in timestamps]
    spans = [(t.start, t.end) for t in timestamps]
    emit("real WordBoundary tokens (word start-end seconds):")
    for t in timestamps:
        emit(f"  {t.word!r:<16} {t.start:7.3f} - {t.end:7.3f}")

    try:
        from manimgen.renderer.tts import save_timestamps

        save_timestamps(timestamps, os.path.join(out_dir, "narration_timestamps.json"))
    except OSError:
        pass

    checks.append(evaluate_duration(duration, len(spoken)))
    checks.append(evaluate_word_coverage(spoken, boundary_words))
    checks.append(evaluate_monotonic(spans, duration))

    try:
        aligned = align_cue_indices(clean, split_cues, boundary_words)
        segs = compute_segments(
            timestamps, split_cues, duration or 0.1, clean_text=clean
        )
        checks.append(
            evaluate_cues(
                spoken,
                split_cues,
                aligned,
                boundary_words,
                [(s.start_time, s.duration) for s in segs],
                duration or 0.1,
            )
        )
    except Exception as e:
        checks.append(Check("cue mapping", FAIL, f"{type(e).__name__}: {_one_line(e)}"))
    return checks


def run_survey(out_dir: str, emit) -> list[Check]:
    emit("")
    emit("Tokenization survey (real WordBoundary tokens vs the test fake):")
    checks: list[Check] = []
    for n, (label, text) in enumerate(SURVEY_SAMPLES, 1):
        path = os.path.join(out_dir, f"survey_{n}.mp3")
        try:
            words = [t.word for t in narrate(text, path)]
        except Exception as e:
            checks.append(Check(f"tokens: {label}", WARN, _one_line(e)))
            emit(f"  [{label}] narration failed: {_one_line(e)}")
            continue
        spoken = text.split()
        emit(f"  [{label}] {format_tokens(spoken, words)}")
        emit(f"    text:       {text}")
        emit(f"    boundaries: {words}")
        checks.append(survey_check(label, spoken, words))
    hyphen = next((w for w in SURVEY_SAMPLES if w[0] == "hyphenated word"), None)
    emit("  Question for tests/test_cue_tokenization.py: is 'well-known' one boundary?")
    if hyphen:
        emit("    see the 'hyphenated word' sample above (one token = fake is right).")
    return checks


def run_all(args: argparse.Namespace, emit=print) -> list[Check]:
    _ensure_project_on_path()
    emit("manimgen TTS smoke test")
    emit("=" * 72)
    try:
        print_config(emit)
    except Exception as e:
        return [
            Check(
                "project import",
                FAIL,
                f"{type(e).__name__}: {_one_line(e)}",
                "Run from the manimgen folder after: pip install -e .",
            )
        ]
    emit("=" * 72)
    try:
        checks = run_main_checks(args.out_dir, emit)
    except ImportError as e:
        return [
            Check(
                "project import",
                FAIL,
                _one_line(e),
                "Run: pip install -r requirements.txt && pip install -e .",
            )
        ]
    for c in checks:
        emit(format_check(c))
    if args.no_survey:
        return checks
    if any(c.name == "narration" and c.status == FAIL for c in checks):
        skip = Check("tokenization survey", SKIP, "narration failed, nothing to survey")
        emit(format_check(skip))
        return [*checks, skip]
    survey = run_survey(args.out_dir, emit)
    for c in survey:
        emit(format_check(c))
    return [*checks, *survey]


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Check that real edge-tts narration and cue timing work here."
    )
    p.add_argument(
        "--out",
        metavar="DIR",
        help="write audio, timestamps and report into DIR instead of a temp folder",
    )
    p.add_argument(
        "--keep", action="store_true", help="keep the temp folder (implied by --out)"
    )
    p.add_argument(
        "--no-survey", action="store_true", help="skip the tokenization survey"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    lines: list[str] = []

    def emit(text: str = "") -> None:
        lines.append(text)
        print(text, flush=True)

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        args.out_dir = os.path.abspath(args.out)
    else:
        args.out_dir = tempfile.mkdtemp(prefix="manimgen_tts_smoke_")
    keep = args.keep or bool(args.out)
    code = 1
    try:
        checks = run_all(args, emit)
        emit(format_summary(checks))
        code = exit_code(checks)
    finally:
        if keep:
            try:
                with open(
                    os.path.join(args.out_dir, "tts_smoke_report.txt"),
                    "w",
                    encoding="utf-8",
                ) as f:
                    f.write("\n".join(lines) + "\n")
            except OSError:
                pass
            print(f"\nOutput kept in: {args.out_dir}")
        else:
            shutil.rmtree(args.out_dir, ignore_errors=True)
    return code


if __name__ == "__main__":
    sys.exit(main())
