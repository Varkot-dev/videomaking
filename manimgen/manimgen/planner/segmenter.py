# Segmenter — converts word timestamps + cue indices into timed animation segments.
#
# Given:
#   timestamps      = [{word, start, end}, ...]  from tts.generate_narration()
#   cue_word_indices = [0, 9, 23]                from lesson plan (parsed by cue_parser)
#   audio_duration   = 14.2                      total audio length in seconds
#
# Produces a list of CueSegment objects, one per animation:
#   segment 0: starts at 0.000s, duration 3.250s  (words 0–8)
#   segment 1: starts at 3.250s, duration 4.850s  (words 9–22)
#   segment 2: starts at 8.100s, duration 6.100s  (words 23–end)
#
# These durations are ground truth — derived from actual spoken audio,
# not estimated. The scene generator uses them as hard constraints so
# the animation fills exactly the time the narrator is speaking.

from manimgen.planner.cue_parser import align_cue_indices
from manimgen.renderer.tts import WordTimestamp, cue_times
from manimgen.types import CueSegment

__all__ = ["CueSegment", "compute_segments", "slice_durations"]


def compute_segments(
    timestamps: list[WordTimestamp],
    cue_word_indices: list[int],
    audio_duration: float,
    clean_text: str | None = None,
) -> list[CueSegment]:
    """Return one CueSegment per cue interval, with exact durations from audio.

    Args:
        timestamps:       Word-level timestamps from TTS (tts.generate_narration).
        cue_word_indices: 0-based word indices marking animation boundaries,
                          always starting with 0. From lesson plan field
                          cue_word_indices. E.g. [0, 9, 23]. These were derived
                          from ``str.split()`` in ``cue_parser.parse_cues``.
        audio_duration:   Total duration of the narration audio file in seconds.
        clean_text:       The narration string that was sent to TTS (no [CUE]
                          tags). When provided, ``cue_word_indices`` is
                          re-derived against the CANONICAL edge-tts WordBoundary
                          tokenization (``timestamps[i].word``) before indexing,
                          because str.split() and edge-tts can tokenize
                          contractions/numbers/hyphens differently. An
                          in-range-but-shifted index would otherwise silently
                          resolve to the wrong word's onset → inaudible A/V
                          desync. When ``None`` (callers passing already-aligned
                          indices, or tests), the indices are used as-is.

    Returns:
        List of CueSegment in order. The durations sum to audio_duration.
    """
    if not cue_word_indices:
        cue_word_indices = [0]

    # Re-derive against the canonical edge-tts token stream when we know the
    # source narration. The edge-tts WordBoundary words are the ground truth
    # the timestamps are indexed by, so the cue indices must agree with them.
    if clean_text is not None:
        cue_word_indices = align_cue_indices(
            clean_text, cue_word_indices, [t.word for t in timestamps]
        )

    starts = cue_times(timestamps, cue_word_indices)
    total = len(starts)
    segments: list[CueSegment] = []

    for i, start in enumerate(starts):
        # Cue 0 audio is sliced from 0.0 (preserving pre-speech silence),
        # so its duration must be measured from 0.0 not from its word onset.
        audio_start = 0.0 if i == 0 else start

        if i < total - 1:
            # Use the .end of the last word in this segment, not the .start
            # of the first word in the next segment. This ensures the last
            # syllable of each cue is not cut off mid-word.
            last_word_idx = cue_word_indices[i + 1] - 1
            # When the next cue starts at word 0 (degenerate/malformed indices),
            # there is no preceding word, so fall back to the next cue's first
            # word .end — never its .start, which would re-introduce the
            # last-syllable clipping the .end boundary exists to prevent.
            boundary = (
                timestamps[last_word_idx].end
                if last_word_idx >= 0
                else timestamps[cue_word_indices[i + 1]].end
            )
            duration = boundary - audio_start
        else:
            duration = audio_duration - audio_start

        segments.append(
            CueSegment(
                cue_index=i,
                total_cues=total,
                start_time=start,
                duration=max(duration, 0.1),  # guard against float rounding to negative
            )
        )

    return segments


def slice_durations(segments: list[CueSegment]) -> list[float]:
    """Length of each cue's audio slice, which is also the cue's video length.

    ``audio_slicer.slice_audio`` cuts cue i from its onset (0.0 for cue 0) to
    the NEXT cue's onset, so the slice includes the pause after the last word.
    ``CueSegment.duration`` stops at that word's end and is shorter by the
    pause (0.3 to 0.4 s after a sentence). A video cut to ``duration`` is
    shorter than its audio, and the muxer fills the gap with a frozen frame.
    These lengths tile the narration exactly, so they sum to the audio length.
    """
    out: list[float] = []
    for i, seg in enumerate(segments):
        if i + 1 < len(segments):
            start = 0.0 if i == 0 else seg.start_time
            out.append(max(segments[i + 1].start_time - start, 0.1))
        else:
            out.append(seg.duration)
    return out
