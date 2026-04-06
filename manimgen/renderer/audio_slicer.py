# Audio slicer — cuts a full-section narration audio file into per-cue segments.
#
# Each CueSegment produced by segmenter.py maps to exactly one audio slice.
# The slice starts at segment.start_time and ends at the next segment's
# start_time (or at the end of the file for the last segment).
#
# Special case — segment 0:
#   start_time is the first-word onset (e.g. 0.113s), not 0.0.
#   We slice from 0.0 so that the natural pre-speech silence is kept.
#   This silence is intentional pacing; stripping it makes the audio feel
#   abrupt and makes A/V sync harder downstream.
#
# Output files: <audio_dir>/<section_id>_cue00.mp3, _cue01.mp3, ...
#
# All slicing is done with FFmpeg. Re-encoding is avoided where possible
# (stream copy for mp3 segments that align on frame boundaries).

import logging
import os
import subprocess

from manimgen.planner.segmenter import CueSegment

logger = logging.getLogger(__name__)

_MIN_SEGMENT_DURATION = 0.5   # warn if a cue segment is shorter than this


def slice_audio(
    audio_path: str,
    segments: list[CueSegment],
    output_dir: str,
    section_id: str,
    overwrite: bool = False,
) -> list[str]:
    """Slice a full-section audio file into one file per CueSegment.

    Args:
        audio_path:  Path to the full-section narration .mp3 file.
        segments:    Ordered list of CueSegment (from segmenter.compute_segments).
        output_dir:  Directory to write sliced files into.
        section_id:  Used to name outputs, e.g. "section_01" →
                     "section_01_cue00.mp3", "section_01_cue01.mp3".
        overwrite:   If True, re-slice even if output already exists.

    Returns:
        List of output file paths in segment order.

    Raises:
        RuntimeError: if ffmpeg is not installed.
        FileNotFoundError: if audio_path does not exist.
    """
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    _check_ffmpeg()
    os.makedirs(output_dir, exist_ok=True)

    if not segments:
        raise ValueError("segments list is empty — nothing to slice")

    # Single segment: copy the whole file (no actual slicing needed)
    if len(segments) == 1:
        out_path = os.path.join(output_dir, f"{section_id}_cue00.mp3")
        if not overwrite and os.path.exists(out_path):
            logger.info("[slicer] Skipping existing: %s", out_path)
            return [out_path]
        _ffmpeg_copy(audio_path, out_path)
        return [out_path]

    output_paths: list[str] = []

    for seg in segments:
        out_path = os.path.join(output_dir, f"{section_id}_cue{seg.cue_index:02d}.mp3")

        if not overwrite and os.path.exists(out_path):
            logger.info("[slicer] Skipping existing: %s", out_path)
            output_paths.append(out_path)
            continue

        if seg.duration < _MIN_SEGMENT_DURATION:
            logger.warning(
                "[slicer] Segment %d is very short (%.2fs) — "
                "audio may sound clipped. Consider adjusting cue placement.",
                seg.cue_index, seg.duration,
            )

        # Segment 0: start from 0.0 to preserve natural pre-speech silence.
        # All other segments: start from their cue onset time.
        start = 0.0 if seg.cue_index == 0 else seg.start_time

        # End time: start_time of the *next* segment for all but the last.
        # For the last segment we let ffmpeg read to EOF (no -to flag).
        is_last = seg.cue_index == seg.total_cues - 1
        if is_last:
            end = None
        else:
            end = segments[seg.cue_index + 1].start_time

        _ffmpeg_slice(audio_path, out_path, start=start, end=end)
        output_paths.append(out_path)

    return output_paths


# ---------------------------------------------------------------------------
# FFmpeg helpers
# ---------------------------------------------------------------------------

def _check_ffmpeg() -> None:
    """Raise RuntimeError if ffmpeg is not on PATH."""
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffmpeg not found. Install FFmpeg to enable audio slicing:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )


def _ffmpeg_slice(
    input_path: str,
    output_path: str,
    start: float,
    end: float | None,
) -> None:
    """Run ffmpeg to extract [start, end) from input into output.

    Uses stream copy (no re-encode) for speed. MP3 stream copy may have
    a small seek imprecision (~26ms per frame) which is acceptable for
    narration cuing — the visual animation cue is also approximate.
    """
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.6f}",
        "-i", input_path,
    ]
    if end is not None:
        cmd += ["-to", f"{end - start:.6f}"]   # relative to -ss when using input seek

    cmd += [
        "-c", "copy",        # stream copy — no re-encode
        "-avoid_negative_ts", "make_zero",
        output_path,
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg slice failed for {output_path}:\n{result.stderr}"
        )


def _ffmpeg_copy(input_path: str, output_path: str) -> None:
    """Copy an audio file unchanged (single-segment fast path)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-c", "copy",
        output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg copy failed for {output_path}:\n{result.stderr}"
        )
