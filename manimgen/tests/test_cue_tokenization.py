# Offline test: cue_parser word-count alignment with edge-tts word boundaries.
#
# The risk: cue_parser uses str.split() to count words, but edge-tts may
# tokenize differently (contractions, punctuation attachment, hyphenated words).
# If they diverge, cue_word_indices point to the wrong words and A/V sync breaks.
#
# These tests used to stream from the real edge-tts service (Microsoft Bing
# speech servers), which made the suite fail or skip whenever the network
# blocked it. They now run fully offline: edge_tts.Communicate is replaced by
# _FakeCommunicate, which emits WordBoundary events the way the real service
# does (one event per spoken word, punctuation dropped, contractions kept
# whole, offsets/durations in 100ns ticks with a leading silence and sentence
# pauses). The code under test (tts._generate_async, cue_parser, segmenter)
# still consumes the exact chunk shape edge-tts produces.
#
# LIMITATION: the fake builds its boundaries from text.split(), so the
# word-count assertions here check the parse_cues / cue_times / compute_segments
# plumbing against an ASSUMED edge-tts tokenization, not against the real
# service. They cannot catch the real service tokenizing differently. To
# validate the assumption, record real WordBoundary output on a machine with
# network access and compare it with _FakeCommunicate.

import re

import pytest

from manimgen.planner.cue_parser import align_cue_indices, parse_cues
from manimgen.planner.segmenter import compute_segments
from manimgen.renderer import tts

# ---------------------------------------------------------------------------
# Fake edge-tts stream
# ---------------------------------------------------------------------------

_TICKS_PER_SEC = 10_000_000  # edge-tts offsets are in 100-nanosecond units

# Timing model loosely matching en-US-AndrewMultilingualNeural at +5%:
# ~0.1s leading silence, ~55ms per character plus a base cost per word,
# short inter-word gaps and longer pauses after clause/sentence punctuation.
_LEAD_IN_SEC = 0.1
_WORD_BASE_SEC = 0.12
_PER_CHAR_SEC = 0.055
_GAP_SEC = 0.04
_CLAUSE_PAUSE_SEC = 0.18
_SENTENCE_PAUSE_SEC = 0.35
_TRAIL_SEC = 0.3

# Leading/trailing punctuation that edge-tts never reports as part of a word.
_EDGE_PUNCT = re.compile(r"^[^\w]+|[^\w]+$")


def _edge_tokens(text: str, split_hyphens: bool = False) -> list[tuple[str, str]]:
    """Tokenize like edge-tts WordBoundary: return (word, trailing_punct) pairs.

    Words are whitespace separated with surrounding punctuation stripped and
    punctuation-only tokens dropped. Internal apostrophes stay ("you'll").
    With split_hyphens=True, "well-known" becomes two boundaries, which is the
    divergent case the cue re-alignment logic exists to handle.
    """
    tokens: list[tuple[str, str]] = []
    for raw in text.split():
        word = _EDGE_PUNCT.sub("", raw)
        if not word:
            continue
        trailing = raw[raw.rfind(word) + len(word) :]
        parts = word.split("-") if split_hyphens else [word]
        parts = [p for p in parts if p]
        for i, part in enumerate(parts):
            tokens.append((part, trailing if i == len(parts) - 1 else ""))
    return tokens


def _fake_boundaries(text: str, split_hyphens: bool = False) -> list[dict]:
    """Build realistic WordBoundary chunks for text."""
    events = []
    t = _LEAD_IN_SEC
    for word, trailing in _edge_tokens(text, split_hyphens):
        dur = _WORD_BASE_SEC + _PER_CHAR_SEC * len(word)
        events.append(
            {
                "type": "WordBoundary",
                "offset": round(t * _TICKS_PER_SEC),
                "duration": round(dur * _TICKS_PER_SEC),
                "text": word,
            }
        )
        t += dur + _GAP_SEC
        if any(c in trailing for c in ".!?"):
            t += _SENTENCE_PAUSE_SEC
        elif any(c in trailing for c in ",;:"):
            t += _CLAUSE_PAUSE_SEC
    return events


def _fake_audio_duration(text: str, split_hyphens: bool = False) -> float:
    """Total audio length the fake stream represents (last word end + tail)."""
    events = _fake_boundaries(text, split_hyphens)
    if not events:
        return _LEAD_IN_SEC + _TRAIL_SEC
    last = events[-1]
    return (last["offset"] + last["duration"]) / _TICKS_PER_SEC + _TRAIL_SEC


class _FakeCommunicate:
    """Drop-in for edge_tts.Communicate that never touches the network."""

    instances: list["_FakeCommunicate"] = []
    split_hyphens = False

    def __init__(self, text, voice, rate="+0%", boundary="SentenceBoundary", **kw):
        self.text = text
        self.voice = voice
        self.rate = rate
        self.boundary = boundary
        _FakeCommunicate.instances.append(self)

    async def stream(self):
        # Interleave audio and boundary chunks the way the service does.
        for event in _fake_boundaries(self.text, self.split_hyphens):
            yield {"type": "audio", "data": b"\xff\xf3" + b"\x00" * 30}
            if self.boundary == "WordBoundary":
                yield dict(event)
        yield {"type": "audio", "data": b"\xff\xf3" + b"\x00" * 30}


@pytest.fixture(autouse=True)
def fake_edge_tts(monkeypatch):
    """Route every edge_tts.Communicate use in this module to the fake."""
    _FakeCommunicate.instances = []
    _FakeCommunicate.split_hyphens = False
    monkeypatch.setattr(tts.edge_tts, "Communicate", _FakeCommunicate)
    return _FakeCommunicate


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tts_words(text: str, tmp_path) -> list[str]:
    """Run the real tts.generate_narration path and return the boundary words."""
    _, timestamps = tts.generate_narration(text, str(tmp_path / "narration.mp3"))
    return [t.word for t in timestamps]


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
def test_word_count_matches_split(label: str, text: str, tmp_path) -> None:
    """TTS word boundary count must equal str.split() count (±1 tolerance).

    A mismatch here means cue indices will point to the wrong word onset
    and audio-animation sync will be off by that many words.

    Tolerance of ±1 accounts for leading/trailing silence tokens that some
    TTS engines emit as zero-duration boundary events.
    """
    words = _tts_words(text, tmp_path)
    split_count = _split_word_count(text)
    assert abs(len(words) - split_count) <= 1, (
        f"[{label}] Word count mismatch: str.split()={split_count}, "
        f"edge-tts WordBoundary={len(words)}. "
        f"Text: {text!r}"
    )
    # Boundary words carry no attached punctuation, so the cue aligner must
    # compare on alphanumeric content rather than raw token equality.
    assert all(w and w[-1].isalnum() for w in words), words


def test_generate_narration_requests_word_boundaries(fake_edge_tts, tmp_path) -> None:
    """generate_narration must ask edge-tts for WordBoundary events.

    Without boundary="WordBoundary" the service only emits sentence events,
    timestamps come back empty and every cue lookup fails.
    """
    tts.generate_narration("Hello there world.", str(tmp_path / "a.mp3"))
    assert len(fake_edge_tts.instances) == 1
    call = fake_edge_tts.instances[0]
    assert call.boundary == "WordBoundary"
    assert call.text == "Hello there world."
    assert re.fullmatch(r"[+-]\d+%", call.rate), call.rate


def test_cue_index_resolves_to_correct_word(tmp_path) -> None:
    """A cue placed after N words must correspond to the correct onset time.

    Verifies the full chain: parse_cues → cue_word_indices → TTS timestamps →
    cue_times returns a timestamp that matches word N from TTS boundaries.
    """
    narration_with_cues = (
        "Start here we go. [CUE] Now this is the next idea. [CUE] And we finish."
    )
    clean, cue_indices = parse_cues(narration_with_cues)

    # word 0 = "Start", word 4 = "Now", word 10 = "And"
    assert cue_indices == [0, 4, 10], cue_indices

    tmp_audio = str(tmp_path / "narration.mp3")
    _, timestamps = tts.generate_narration(clean, tmp_audio)
    with open(tmp_audio, "rb") as f:
        assert f.read(), "generate_narration wrote an empty audio file"

    # All cue indices must be valid (within bounds of TTS word count)
    assert len(timestamps) > 0, "TTS returned no word timestamps"
    for idx in cue_indices:
        assert idx < len(timestamps), (
            f"Cue index {idx} out of range: TTS only has {len(timestamps)} words. "
            f"str.split() and edge-tts have diverged."
        )

    # Each cue must land on the word that followed its [CUE] tag.
    assert [timestamps[i].word for i in cue_indices] == ["Start", "Now", "And"]

    # cue_times must not raise and must return monotonically increasing times
    times = tts.cue_times(timestamps, cue_indices)
    assert len(times) == len(cue_indices)
    for i in range(1, len(times)):
        assert times[i] >= times[i - 1], (
            f"Cue times not monotonically increasing: {times}"
        )
    # And they must be exactly those words' onsets.
    assert times == [timestamps[i].start for i in cue_indices]
    assert times[0] == pytest.approx(_LEAD_IN_SEC)


def test_hyphen_split_divergence_is_realigned(fake_edge_tts, tmp_path) -> None:
    """If edge-tts splits "well-known" into two boundaries, cues must follow.

    str.split() sees one word, edge-tts two, so every cue after the hyphen is
    shifted by one. align_cue_indices / compute_segments(clean_text=...) must
    re-derive the index so the cue still starts on the intended word.
    """
    fake_edge_tts.split_hyphens = True
    clean, cue_indices = parse_cues(
        "This is a well-known technique. [CUE] Computers use it everywhere."
    )
    assert cue_indices == [0, 5]

    _, timestamps = tts.generate_narration(clean, str(tmp_path / "n.mp3"))
    words = [t.word for t in timestamps]
    assert len(words) == _split_word_count(clean) + 1

    aligned = align_cue_indices(clean, cue_indices, words)
    assert [words[i] for i in aligned] == ["This", "Computers"]

    audio_dur = _fake_audio_duration(clean, split_hyphens=True)
    segments = compute_segments(timestamps, cue_indices, audio_dur, clean_text=clean)
    assert segments[1].duration == pytest.approx(
        audio_dur - timestamps[aligned[1]].start, abs=1e-3
    )


def test_short_cue_segment_not_negative(tmp_path) -> None:
    """A cue placed just 1 word before the end must still yield a positive duration."""
    narration = "One two three. [CUE] Four."
    clean, cue_indices = parse_cues(narration)
    assert cue_indices == [0, 3]

    _, timestamps = tts.generate_narration(clean, str(tmp_path / "narration.mp3"))
    # ffprobe cannot read the fake MP3 bytes, so use the duration the fake
    # stream represents (last word end plus trailing silence).
    audio_dur = _fake_audio_duration(clean)
    assert audio_dur > timestamps[-1].end

    segments = compute_segments(timestamps, cue_indices, audio_dur)
    assert len(segments) == 2
    for seg in segments:
        assert seg.duration > 0, (
            f"Segment {seg.cue_index} has non-positive duration: {seg.duration}"
        )
    # The last cue runs from its word onset to the end of the audio.
    assert segments[1].duration == pytest.approx(
        audio_dur - timestamps[3].start, abs=1e-3
    )
