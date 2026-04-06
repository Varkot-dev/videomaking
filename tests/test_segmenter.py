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
        expected = AUDIO_DURATION - TIMESTAMPS[0].start
        assert abs(segs[0].duration - expected) < 1e-6

    def test_two_cues_returns_two_segments(self):
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        assert len(segs) == 2

    def test_two_cues_durations_sum_to_spoken_duration(self):
        # Segments cover from first word to end of audio (pre-speech silence excluded)
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        total = sum(s.duration for s in segs)
        expected = AUDIO_DURATION - TIMESTAMPS[0].start
        assert abs(total - expected) < 1e-6

    def test_three_cues_correct_durations(self):
        # cue at word 0 (0.10s), word 5 (1.92s), word 9 (3.40s)
        segs = compute_segments(TIMESTAMPS, [0, 5, 9], AUDIO_DURATION)
        assert len(segs) == 3
        # seg 0: 0.10 → 1.92 = 1.82s
        assert abs(segs[0].duration - (1.92 - 0.10)) < 1e-6
        # seg 1: 1.92 → 3.40 = 1.48s
        assert abs(segs[1].duration - (3.40 - 1.92)) < 1e-6
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
