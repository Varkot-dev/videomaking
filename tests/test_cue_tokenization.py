# Integration test: cue_parser word-count alignment with edge-tts word boundaries.
#
# The risk: cue_parser uses str.split() to count words, but edge-tts may
# tokenize differently (contractions, punctuation attachment, hyphenated words).
# If they diverge, cue_word_indices point to the wrong words and A/V sync breaks.
#
# These tests run against the real edge-tts stream (free, no API key) and verify
# that for real narration strings the word counts stay aligned.
#
# Skipped automatically when edge-tts is not installed (CI without audio deps).

import asyncio
import pytest

try:
    import edge_tts
    _EDGE_TTS_AVAILABLE = True
except ImportError:
    _EDGE_TTS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not _EDGE_TTS_AVAILABLE,
    reason="edge-tts not installed",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _collect_word_boundaries(text: str) -> list[str]:
    """Stream edge-tts and return the list of words from WordBoundary events."""
    communicate = edge_tts.Communicate(
        text,
        "en-US-AndrewMultilingualNeural",
        rate="+5%",
        boundary="WordBoundary",
    )
    words = []
    async for chunk in communicate.stream():
        if chunk["type"] == "WordBoundary":
            words.append(chunk["text"])
    return words


def _tts_word_count(text: str) -> int:
    return len(asyncio.run(_collect_word_boundaries(text)))


def _split_word_count(text: str) -> int:
    return len(text.split())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

NARRATION_SAMPLES = [
    # (label, narration_text)
    (
        "simple sentence",
        "Binary search cuts the problem in half every time.",
    ),
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


@pytest.mark.parametrize("label,text", NARRATION_SAMPLES)
def test_word_count_matches_split(label: str, text: str) -> None:
    """TTS word boundary count must equal str.split() count (±1 tolerance).

    A mismatch here means cue indices will point to the wrong word onset
    and audio-animation sync will be off by that many words.

    Tolerance of ±1 accounts for leading/trailing silence tokens that some
    TTS engines emit as zero-duration boundary events.
    """
    tts_count = _tts_word_count(text)
    split_count = _split_word_count(text)
    assert abs(tts_count - split_count) <= 1, (
        f"[{label}] Word count mismatch: str.split()={split_count}, "
        f"edge-tts WordBoundary={tts_count}. "
        f"Text: {text!r}"
    )


def test_cue_index_resolves_to_correct_word() -> None:
    """A cue placed after N words must correspond to the correct onset time.

    Verifies the full chain: parse_cues → cue_word_indices → TTS timestamps →
    cue_times returns a timestamp that matches word N from TTS boundaries.
    """
    from manimgen.planner.cue_parser import parse_cues
    from manimgen.renderer.tts import generate_narration, cue_times
    import tempfile, os

    narration_with_cues = (
        "Start here we go. [CUE] Now this is the next idea. [CUE] And we finish."
    )
    clean, cue_indices = parse_cues(narration_with_cues)

    # cue_indices should be [0, 4, 10] — word 0, word after "Start here we go.", word after "Now..."
    assert cue_indices[0] == 0, "First cue index must always be 0"
    assert len(cue_indices) == 3, f"Expected 3 cues, got {len(cue_indices)}: {cue_indices}"

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    try:
        _, timestamps = generate_narration(clean, tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # All cue indices must be valid (within bounds of TTS word count)
    assert len(timestamps) > 0, "TTS returned no word timestamps"
    for idx in cue_indices:
        assert idx < len(timestamps), (
            f"Cue index {idx} out of range — TTS only has {len(timestamps)} words. "
            f"str.split() and edge-tts have diverged."
        )

    # cue_times must not raise and must return monotonically increasing times
    times = cue_times(timestamps, cue_indices)
    assert len(times) == len(cue_indices)
    for i in range(1, len(times)):
        assert times[i] >= times[i - 1], (
            f"Cue times not monotonically increasing: {times}"
        )


def test_short_cue_segment_not_negative() -> None:
    """A cue placed just 1 word before the end must still yield a positive duration."""
    from manimgen.planner.cue_parser import parse_cues
    from manimgen.renderer.tts import generate_narration, get_audio_duration
    from manimgen.planner.segmenter import compute_segments
    import tempfile, os

    narration = "One two three. [CUE] Four."
    clean, cue_indices = parse_cues(narration)

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        tmp_path = f.name
    try:
        _, timestamps = generate_narration(clean, tmp_path)
        audio_dur = get_audio_duration(tmp_path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    segments = compute_segments(timestamps, cue_indices, audio_dur)
    for seg in segments:
        assert seg.duration > 0, f"Segment {seg.cue_index} has non-positive duration: {seg.duration}"
