"""
Tests for manimgen/planner/segmenter.py — pure logic, no LLM, no network.
"""

import pytest
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
