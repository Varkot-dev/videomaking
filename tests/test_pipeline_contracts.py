"""
Pipeline contract tests — catch cross-module A/V sync bugs.

These tests verify the invariants BETWEEN modules, not within them.
The unit tests for each module (test_segmenter.py, test_audio_slicer.py,
test_muxer_clean.py) test internal behaviour in isolation.

This file tests the contracts:
  segmenter → audio_slicer → muxer

A bug that passes all unit tests but still causes A/V drift should
fail at least one test here.

No LLM, no network. TTS tests are skipped without edge-tts.
"""

import pytest
from unittest.mock import patch, MagicMock

from manimgen.renderer.tts import WordTimestamp
from manimgen.planner.segmenter import CueSegment, compute_segments
from manimgen.renderer.audio_slicer import slice_audio


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _ts(word, start, end):
    return WordTimestamp(word=word, start=start, end=end)


def _seg(cue_index, total, start_time, duration):
    return CueSegment(
        cue_index=cue_index,
        total_cues=total,
        start_time=start_time,
        duration=duration,
    )


# A 10-word narration with deliberate inter-word gaps (realistic).
TIMESTAMPS = [
    _ts("Binary",  0.10, 0.50),
    _ts("search",  0.52, 1.00),
    _ts("cuts",    1.05, 1.30),
    _ts("the",     1.32, 1.42),
    _ts("problem", 1.45, 1.90),
    _ts("in",      1.92, 2.00),
    _ts("half",    2.05, 2.50),
    _ts("every",   2.55, 2.90),
    _ts("time",    2.95, 3.30),
    _ts("Watch",   3.40, 3.80),
]
AUDIO_DURATION = 4.20


# ---------------------------------------------------------------------------
# Contract: segmenter → audio_slicer
#
# The slicer cuts audio using segment.start_time as the seek position
# and segments[i+1].start_time as the end boundary.
# The segmenter must produce start_times that are consistent with what
# the slicer will actually cut.
# ---------------------------------------------------------------------------

class TestSegmenterToSlicerContract:
    """Segmenter output must be internally consistent for the slicer to cut correctly."""

    def test_segment_zero_start_time_is_word_onset_not_zero(self):
        """
        segmenter.start_time for cue 0 = first word onset (e.g. 0.10s).
        The slicer explicitly ignores this and seeks from 0.0 to preserve
        pre-speech silence. These two must agree on the contract:
        start_time carries the word onset; the slicer applies the 0.0 override.
        """
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        # start_time is the word onset — used by slicer only for cues 1+
        assert abs(segs[0].start_time - TIMESTAMPS[0].start) < 1e-6
        # duration is measured from 0.0 (what slicer actually does for cue 0)
        assert segs[0].duration > segs[0].start_time  # duration > word onset

    def test_segment_zero_duration_covers_from_audio_start(self):
        """
        cue 0 duration must equal (boundary - 0.0), not (boundary - word_onset).
        If it used word_onset, the animation would run short by ~100ms —
        exactly the pre-speech silence the slicer preserves.
        """
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        # boundary is word4.end = 1.90; audio starts at 0.0
        assert abs(segs[0].duration - 1.90) < 1e-6

    def test_last_word_of_cue_not_clipped(self):
        """
        The boundary between cues must use the LAST word's .end (not the
        next cue's first word .start). Otherwise the last syllable of each
        cue is cut off because the slicer uses segment.start_time of the
        next segment as the end point.

        cue 0 ends before word index 5 ("in", start=1.92, but word 4
        "problem" ends at 1.90). Boundary = 1.90 (word.end), not 1.92 (word.start).
        """
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        # If boundary were word5.start (1.92), duration would be 1.92
        # If boundary is word4.end (1.90), duration is 1.90
        assert abs(segs[0].duration - 1.90) < 1e-6, (
            f"Expected 1.90 (word4.end boundary) but got {segs[0].duration}. "
            "The last syllable of cue 0 would be clipped if boundary = next word onset."
        )

    def test_non_zero_segment_start_time_matches_word_onset(self):
        """
        For cues 1+, start_time must be the cue word's onset — the slicer seeks
        to this position. If it's wrong, audio cuts in mid-syllable.
        """
        segs = compute_segments(TIMESTAMPS, [0, 5, 9], AUDIO_DURATION)
        assert abs(segs[1].start_time - TIMESTAMPS[5].start) < 1e-6
        assert abs(segs[2].start_time - TIMESTAMPS[9].start) < 1e-6

    def test_slicer_end_boundary_equals_next_segment_start_time(self):
        """
        The slicer computes the end of segment i as segments[i+1].start_time.
        So: slicer_end_of_seg_0 = segs[1].start_time = 1.92.
        But segmenter.duration for seg 0 = 1.90 (word4.end).
        This means the slicer cuts slightly MORE audio (1.92s) than the
        segmenter reports as duration (1.90s). The difference (0.02s) is the
        natural inter-word gap — acceptable and by design.

        This test documents this known discrepancy so it is not mistaken for a bug.
        """
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        slicer_end = segs[1].start_time  # what the slicer uses
        segmenter_dur = segs[0].duration  # what the scene generator gets

        # slicer end >= segmenter duration (includes the inter-word gap)
        assert slicer_end >= segmenter_dur, (
            "Slicer would cut audio BEFORE the segmenter-reported duration ends. "
            "Scene generator would receive more time than audio provides."
        )
        # Gap must be small (< 200ms inter-word gap)
        assert (slicer_end - segmenter_dur) < 0.2, (
            f"Gap between slicer end and segmenter duration is {slicer_end - segmenter_dur:.3f}s. "
            "This is too large to be just an inter-word gap."
        )

    def test_all_segment_durations_positive(self):
        """No segment can have zero or negative duration — manimgl would crash."""
        segs = compute_segments(TIMESTAMPS, [0, 3, 7, 9], AUDIO_DURATION)
        for seg in segs:
            assert seg.duration > 0, (
                f"Segment {seg.cue_index} has non-positive duration {seg.duration}. "
                "ManimGL scene would be given 0s to animate."
            )

    def test_durations_cover_substantially_all_audio(self):
        """
        Sum of durations should be close to audio_duration. Large gaps mean
        the scene generator is given durations that don't account for all the
        spoken audio — the video will run short.
        """
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)
        total = sum(s.duration for s in segs)
        # Should be within 5% of audio_duration
        assert total >= AUDIO_DURATION * 0.90, (
            f"Segment durations sum to {total:.3f}s but audio is {AUDIO_DURATION}s. "
            "More than 10% of audio is unaccounted for."
        )


# ---------------------------------------------------------------------------
# Contract: slicer start times vs. segment 0 pre-speech silence rule
#
# The slicer has a hardcoded rule: cue_index == 0 → seek from 0.0.
# The segmenter has a mirror rule: i == 0 → audio_start = 0.0.
# Both rules must agree. If one changes without the other, sync breaks.
# ---------------------------------------------------------------------------

class TestCueZeroSilenceContract:
    """The pre-speech silence preservation rule must be consistent across both modules."""

    def test_slicer_cue0_seeks_from_zero(self, tmp_path):
        """Slicer must pass -ss 0.0 for cue 0 regardless of segment.start_time."""
        audio = tmp_path / "narration.mp3"
        audio.write_bytes(b"fake")
        # Simulate cue 0 with word onset at 0.113s (real edge-tts output)
        segments = [
            _seg(0, 2, 0.113, 3.137),
            _seg(1, 2, 3.250, 4.850),
        ]

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ok()) as mock_run:
            slice_audio(str(audio), segments, str(tmp_path), "s01")

        first_cmd = mock_run.call_args_list[0][0][0]
        ss_idx = first_cmd.index("-ss")
        ss_val = float(first_cmd[ss_idx + 1])
        assert abs(ss_val - 0.0) < 1e-6, (
            f"Slicer started cue 0 at {ss_val}s instead of 0.0. "
            "Pre-speech silence is stripped — audio will feel abrupt."
        )

    def test_segmenter_cue0_duration_includes_pre_speech_silence(self):
        """
        Segmenter must measure cue 0 duration from 0.0 (not from word onset).
        If it measures from word onset (0.10), the scene gets 0.10s less time
        than the audio actually plays — video ends before narration does.
        """
        segs = compute_segments(TIMESTAMPS, [0], AUDIO_DURATION)
        # Single cue: duration must be full audio_duration (measured from 0.0)
        assert abs(segs[0].duration - AUDIO_DURATION) < 1e-6, (
            f"Single-cue duration is {segs[0].duration}s, expected {AUDIO_DURATION}s. "
            f"If it's {AUDIO_DURATION - TIMESTAMPS[0].start:.2f}s, the module is "
            "measuring from word onset instead of audio start."
        )

    def test_two_modules_agree_on_cue0_audio_start(self):
        """
        Both modules must agree: cue 0 audio starts at 0.0, not word onset.
        The segmenter's duration for cue 0 is measured from 0.0.
        The slicer seeks cue 0 from 0.0.
        If they disagree, the scene gets a duration that doesn't match the slice.
        """
        segs = compute_segments(TIMESTAMPS, [0, 5], AUDIO_DURATION)

        # Segmenter says cue 0 lasts from 0.0 → boundary
        segmenter_audio_start = 0.0
        segmenter_end = segmenter_audio_start + segs[0].duration  # = 1.90

        # Slicer will seek from 0.0 and cut at segs[1].start_time = 1.92
        slicer_start = 0.0
        slicer_end = segs[1].start_time  # = 1.92

        # Both start at 0.0
        assert abs(segmenter_audio_start - slicer_start) < 1e-9, (
            "Modules disagree on cue 0 audio start."
        )
        # Slicer end is slightly after segmenter end (inter-word gap) — not before
        assert slicer_end >= segmenter_end - 1e-6, (
            f"Slicer cuts at {slicer_end}s but segmenter reports duration ending at "
            f"{segmenter_end}s. Audio would be cut before narration ends."
        )


# ---------------------------------------------------------------------------
# Contract: muxer -t flag prevents race conditions
#
# Previously, muxer used -shortest. That created a race: if the audio stream
# was slightly longer than the padded video, ffmpeg would truncate audio.
# The fix was -t {video_dur}. This test suite verifies that contract.
# ---------------------------------------------------------------------------

class TestMuxerDurationContract:

    def test_muxer_uses_explicit_duration_not_shortest(self, tmp_path):
        """
        The apad path must NOT use -shortest. It must use -t <video_dur>.
        -shortest truncates at whichever stream ends first — a race condition
        that can cut off the last 50-100ms of narration.
        """
        from manimgen.renderer.muxer import mux_audio_video

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("manimgen.renderer.muxer._get_duration", side_effect=[10.0, 9.0, 10.0]), \
             patch("manimgen.renderer.muxer.subprocess.run", side_effect=fake_run), \
             patch("os.makedirs"):
            mux_audio_video("v.mp4", "a.mp3", str(tmp_path / "out.mp4"))

        cmd_str = " ".join(calls[0])
        assert "-shortest" not in cmd_str, (
            "muxer uses -shortest in apad path. This is a race condition that "
            "can truncate the last 100ms of narration."
        )
        assert "-t" in cmd_str, (
            "muxer apad path must use -t <video_dur> to pin output duration exactly."
        )

    def test_muxer_tpad_duration_is_gap_not_total(self, tmp_path):
        """
        tpad stop_duration must be the GAP (audio - video), not the total audio.
        If it uses total audio duration, the video is padded by audio_dur seconds
        AFTER its end, making the output 2× too long.
        """
        from manimgen.renderer.muxer import mux_audio_video

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        video_dur = 5.0
        audio_dur = 8.0
        expected_gap = audio_dur - video_dur  # 3.0

        with patch("manimgen.renderer.muxer._get_duration",
                   side_effect=[video_dur, audio_dur, video_dur]), \
             patch("manimgen.renderer.muxer.subprocess.run", side_effect=fake_run), \
             patch("os.makedirs"):
            mux_audio_video("v.mp4", "a.mp3", str(tmp_path / "out.mp4"))

        cmd_str = " ".join(calls[0])
        assert f"stop_duration={expected_gap:.6f}" in cmd_str, (
            f"tpad stop_duration should be the gap ({expected_gap}s) not total audio ({audio_dur}s). "
            "Using total audio duration pads by audio_dur seconds after video ends."
        )
        # Make sure total audio duration is NOT used as stop_duration
        assert f"stop_duration={audio_dur:.6f}" not in cmd_str, (
            f"tpad stop_duration={audio_dur} found — this is total audio duration, not the gap. "
            "Output would be nearly twice as long as intended."
        )


# ---------------------------------------------------------------------------
# Contract: assembler sample rate consistency
#
# All ffmpeg commands in assembler must enforce 48kHz. If any re-encode
# drops the -ar 48000 flag, each xfade or concat creates a sample-rate
# step that accumulates into audible pitch/speed drift over a long video.
# ---------------------------------------------------------------------------

class TestAssemblerSampleRateContract:

    def _collect_assembler_commands(self, tmp_path, video_paths):
        """Run assembler internals and capture all ffmpeg commands issued."""
        from manimgen.renderer import assembler

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            m.stdout = "5.0\n"
            return m

        # Patch at the module level
        with patch.object(assembler.subprocess, "run", side_effect=fake_run):
            try:
                assembler._normalise_all(video_paths, str(tmp_path))
            except Exception:
                pass  # we only care about the commands captured

        return commands

    def test_normalise_all_enforces_48khz(self, tmp_path):
        """_normalise_all must include -ar 48000 — first re-encode sets the baseline."""
        from manimgen.renderer import assembler
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            return m

        with patch.object(assembler.subprocess, "run", side_effect=fake_run):
            try:
                assembler._normalise_all(["a.mp4", "b.mp4"], str(tmp_path))
            except Exception:
                pass

        for cmd in commands:
            cmd_str = " ".join(cmd)
            assert "-ar" in cmd_str and "48000" in cmd_str, (
                f"_normalise_all command missing -ar 48000: {cmd_str}. "
                "Sample rate mismatch will cause drift in subsequent re-encodes."
            )

    def test_hard_concat_uses_stream_copy(self, tmp_path):
        """_hard_concat must use stream copy (-c copy) — inputs are already normalised
        to 48kHz by _normalise_all, so re-encoding would be a lossy no-op."""
        from manimgen.renderer import assembler
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            return m

        with patch.object(assembler.subprocess, "run", side_effect=fake_run):
            try:
                assembler._hard_concat(["a.mp4", "b.mp4"], str(tmp_path / "out.mp4"))
            except Exception:
                pass

        assert commands, "Expected at least one ffmpeg call"
        cmd_str = " ".join(commands[0])
        assert "-c copy" in cmd_str or ("-c" in cmd_str and "copy" in cmd_str), (
            f"_hard_concat must use stream copy, not re-encode: {cmd_str}. "
            "Inputs are already 48kHz from _normalise_all."
        )

    def test_xfade_pair_enforces_48khz(self, tmp_path):
        """_xfade_pair must include -ar 48000 in its acrossfade re-encode."""
        from manimgen.renderer import assembler
        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            m.stdout = "5.0\n"
            return m

        with patch.object(assembler.subprocess, "run", side_effect=fake_run):
            try:
                assembler._xfade_pair("a.mp4", "b.mp4", str(tmp_path / "out.mp4"))
            except Exception:
                pass

        for cmd in commands:
            cmd_str = " ".join(cmd)
            if "xfade" in cmd_str or "acrossfade" in cmd_str:
                assert "48000" in cmd_str, (
                    f"_xfade_pair acrossfade command missing 48000: {cmd_str}. "
                    "Each xfade that re-encodes without -ar 48000 introduces rate drift."
                )


# ---------------------------------------------------------------------------
# Contract: audio slicer produces AAC, not MP3 stream copy
#
# MP3 stream copy snaps to ~26ms frame boundaries. With 10 cue slices,
# this compounds to ~260ms of drift. AAC re-encodes at sample level (<0.1ms).
# ---------------------------------------------------------------------------

class TestSlicerAacContract:

    def test_no_stream_copy_in_any_slice(self, tmp_path):
        """No ffmpeg command from the slicer may use stream copy (-c copy)."""
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake")
        segments = [_seg(i, 5, float(i), 1.0) for i in range(5)]
        segments[0] = _seg(0, 5, 0.1, 0.9)  # cue 0 word onset

        commands = []

        def fake_run(cmd, **kwargs):
            commands.append(list(cmd))
            m = MagicMock()
            m.returncode = 0
            m.stderr = ""
            return m

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   side_effect=fake_run):
            slice_audio(str(audio), segments, str(tmp_path), "test")

        for cmd in commands:
            cmd_str = " ".join(cmd)
            # No bare "copy" as a codec value
            assert "-c copy" not in cmd_str, (
                "Stream copy found in slicer output. MP3 stream copy snaps to "
                "~26ms frame boundaries, compounding to >200ms drift across 10 cues."
            )
            # Must explicitly use AAC
            assert "aac" in cmd_str, (
                f"Slicer command does not use AAC: {cmd_str}. "
                "Must re-encode to AAC for sample-accurate cuts."
            )

    def test_output_files_are_m4a_not_mp3(self, tmp_path):
        """Output extension must be .m4a (AAC in mp4 container), not .mp3."""
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"fake")
        segments = [_seg(0, 2, 0.1, 2.0), _seg(1, 2, 2.1, 3.0)]

        with patch("manimgen.renderer.audio_slicer._check_ffmpeg"), \
             patch("manimgen.renderer.audio_slicer.subprocess.run",
                   return_value=_mock_ok()):
            paths = slice_audio(str(audio), segments, str(tmp_path), "sec01")

        for p in paths:
            assert p.endswith(".m4a"), (
                f"Slicer produced {p!r} — must be .m4a. "
                "MP3 container cannot hold AAC audio."
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_ok():
    m = MagicMock()
    m.returncode = 0
    m.stderr = ""
    return m
