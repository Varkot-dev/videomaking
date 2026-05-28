"""
Tests for manimgen/planner/segmenter.py — pure logic, no LLM, no network.
"""

from manimgen.renderer.tts import WordTimestamp
from manimgen.planner.segmenter import CueSegment, compute_segments


def _make_timestamps(words_with_times: list[tuple[str, float, float]]) -> list[WordTimestamp]:
    return [WordTimestamp(word=w, start=s, end=e) for w, s, e in words_with_times]


# 10-word narration with known timestamps
TIMESTAMPS = _make_timestamps([
    ("Binary",    0.10, 0.50),
    ("search",    0.52, 1.00),
    ("cuts",      1.05, 1.30),
    ("the",       1.32, 1.42),
    ("problem",   1.45, 1.90),
    ("in",        1.92, 2.00),
    ("half",      2.05, 2.50),
    ("every",     2.55, 2.90),
    ("time",      2.95, 3.30),
    ("Watch",     3.40, 3.80),
])
AUDIO_DURATION = 4.20


class TestComputeSegments:

    def test_single_cue_returns_one_segment(self):
        segs = compute_segments(TIMESTAMPS, [0], AUDIO_DURATION)
        assert len(segs) == 1
        assert segs[0].cue_index == 0
        assert segs[0].total_cues == 1

    def test_single_segment_duration_spans_first_word_to_end(self):
        # Duration = audio_duration - first_word_start (pre-speech silence is excluded)
        segs = compute_segments(TIMESTAMPS, [0], AUDIO_DURATION)
        # Cue 0 audio starts at 0.0 (including pre-speech silence), so duration = full audio
        expected = AUDIO_DURATION - 0.0
        assert abs(segs[0].duration - expected) < 1e-6

    def test_two_cues_returns_two_segments(self):
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        assert len(segs) == 2

    def test_two_cues_durations_cover_audio(self):
        # Cue 0: 0.0 → end of word 4 ("problem", 1.90)
        # Cue 1: start of word 5 ("in", 1.92) → end of audio (4.20)
        # Durations don't sum to audio_duration exactly (small gap between word end and next onset)
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        assert len(segs) == 2
        assert abs(segs[0].duration - 1.90) < 1e-6   # 0.0 → word4.end = 1.90
        assert abs(segs[1].duration - (4.20 - 1.92)) < 1e-6  # word5.start → audio_end

    def test_three_cues_correct_durations(self):
        # cue at word 0 (0.10s), word 5 (1.92s), word 9 (3.40s)
        segs = compute_segments(TIMESTAMPS, [0, 5, 9], AUDIO_DURATION)
        assert len(segs) == 3
        # seg 0: audio starts at 0.0, boundary = word4.end = 1.90 → duration 1.90s
        assert abs(segs[0].duration - 1.90) < 1e-6
        # seg 1: starts at 1.92, boundary = word8.end = 3.30 → duration = 3.30 - 1.92 = 1.38s
        assert abs(segs[1].duration - (3.30 - 1.92)) < 1e-6
        # seg 2: 3.40 → 4.20 = 0.80s
        assert abs(segs[2].duration - (AUDIO_DURATION - 3.40)) < 1e-6

    def test_start_times_match_cue_word_timestamps(self):
        segs = compute_segments(TIMESTAMPS, [0, 5, 9], AUDIO_DURATION)
        assert abs(segs[0].start_time - TIMESTAMPS[0].start) < 1e-6
        assert abs(segs[1].start_time - TIMESTAMPS[5].start) < 1e-6
        assert abs(segs[2].start_time - TIMESTAMPS[9].start) < 1e-6

    def test_cue_indices_are_zero_based_sequential(self):
        segs = compute_segments(TIMESTAMPS, [0, 3, 7], AUDIO_DURATION)
        assert [s.cue_index for s in segs] == [0, 1, 2]

    def test_total_cues_is_consistent(self):
        segs = compute_segments(TIMESTAMPS, [0, 3, 7], AUDIO_DURATION)
        assert all(s.total_cues == 3 for s in segs)

    def test_empty_cue_list_treated_as_single_segment(self):
        segs = compute_segments(TIMESTAMPS, [], AUDIO_DURATION)
        assert len(segs) == 1
        assert segs[0].cue_index == 0

    def test_duration_never_negative(self):
        # Pathological: audio_duration shorter than last cue start (should clamp to 0.1)
        segs = compute_segments(TIMESTAMPS, [0, 9], audio_duration=3.30)
        assert all(s.duration >= 0.1 for s in segs)

    def test_returns_list_of_cue_segments(self):
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        assert isinstance(segs, list)
        assert all(isinstance(s, CueSegment) for s in segs)

    def test_single_word_timestamps(self):
        ts = _make_timestamps([("Hello", 0.0, 0.5)])
        segs = compute_segments(ts, [0], 1.0)
        assert len(segs) == 1
        assert abs(segs[0].duration - 1.0) < 1e-6

    def test_degenerate_next_cue_index_uses_word_end_not_start(self):
        # Malformed indices where the next cue points at word 0 → last_word_idx
        # = -1. The boundary must fall back to that word's .end (0.50), never
        # its .start (0.10), which would clip the last syllable and produce a
        # nonsensical (here negative-before-clamp) duration.
        segs = compute_segments(TIMESTAMPS, [0, 0], AUDIO_DURATION)
        # Cue 0 boundary = TIMESTAMPS[0].end = 0.50, audio_start = 0.0.
        assert abs(segs[0].duration - 0.50) < 1e-6, (
            f"Expected boundary at word0.end (0.50) but got {segs[0].duration}. "
            "Fallback must use .end, not .start (0.10), to match the A/V contract."
        )


class TestCanonicalAlignmentSeam:
    """#36 — compute_segments(clean_text=...) re-derives cue indices against
    the edge-tts WordBoundary tokenization carried in `timestamps`, so an
    in-range-but-shifted str.split index can't silently desync the segment.
    """

    def test_clean_text_none_uses_indices_as_is(self):
        # Back-compat: no clean_text → indices used verbatim (unchanged behaviour).
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        assert abs(segs[1].start_time - TIMESTAMPS[5].start) < 1e-6

    def test_contraction_realigns_segment_start(self):
        # edge-tts split "you'll" into "you" + "'ll", so its WordBoundary stream
        # (and thus `timestamps`) has one MORE token than str.split(). A cue at
        # str.split index 4 ("you'll") must resolve to the onset of "you".
        ts = _make_timestamps([
            ("It",      0.10, 0.30),
            ("'s",      0.30, 0.45),
            ("a",       0.50, 0.60),
            ("classic", 0.62, 1.10),
            ("that",    1.12, 1.40),
            ("you",     1.45, 1.70),   # <- correct onset for the "you'll" cue
            ("'ll",     1.70, 1.85),
            ("use",     1.90, 2.20),
        ])
        clean = "It's a classic that you'll use"  # 6 str.split tokens
        # Raw str.split index 4 = "you'll". Without re-derivation it would index
        # ts[4] = "that" (1.12s). With clean_text it must land on "you" (1.45s).
        segs = compute_segments(ts, [0, 4], 2.40, clean_text=clean)
        assert abs(segs[1].start_time - 1.45) < 1e-6, (
            f"Expected segment to start at 'you' (1.45s), got {segs[1].start_time}"
        )
        # And the buggy raw-index behaviour is demonstrably different:
        raw = compute_segments(ts, [0, 4], 2.40)  # no clean_text
        assert abs(raw[1].start_time - 1.12) < 1e-6  # lands on "that" — the desync

    def test_matching_tokenization_is_noop(self):
        # When edge-tts agrees with str.split, clean_text changes nothing.
        clean = " ".join(t.word for t in TIMESTAMPS)
        with_text = compute_segments(TIMESTAMPS, [0, 5, 9], AUDIO_DURATION, clean_text=clean)
        without = compute_segments(TIMESTAMPS, [0, 5, 9], AUDIO_DURATION)
        assert [s.start_time for s in with_text] == [s.start_time for s in without]
