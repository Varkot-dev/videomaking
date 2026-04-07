"""
Video cutter — slices a single rendered section video into per-cue clips
at exact timestamps derived from the TTS segmenter.

Used by the new single-scene-per-section architecture: the Director generates
one .py file per section, ManimGL renders one .mp4, and this module cuts it
into N cue clips that the muxer then overlays with audio slices.
"""
import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def cut_video_at_cues(
    video_path: str,
    cue_start_times: list[float],
    cue_durations: list[float],
    output_dir: str,
    section_id: str,
) -> list[str]:
    """Cut a section video into per-cue clips using FFmpeg stream copy.

    Args:
        video_path:       Path to the full rendered section .mp4 (silent).
        cue_start_times:  Start time (seconds) of each cue within the video.
        cue_durations:    Duration (seconds) of each cue.
        output_dir:       Directory to write the output clips.
        section_id:       e.g. "section_01" — used to name output files.

    Returns:
        List of output clip paths in cue order.
    """
    os.makedirs(output_dir, exist_ok=True)
    out_paths = []

    for i, (start, dur) in enumerate(zip(cue_start_times, cue_durations)):
        out_path = os.path.join(output_dir, f"{section_id}_cue{i:02d}_video.mp4")
        cmd = [
            "ffmpeg", "-y",
            "-ss", f"{start:.6f}",
            "-i", video_path,
            "-t", f"{dur:.6f}",
            # Re-encode to avoid keyframe alignment issues with stream copy
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            "-pix_fmt", "yuv420p",
            "-an",  # no audio
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            logger.error("[cutter] FFmpeg failed for cue %d: %s", i, result.stderr[-500:])
            raise RuntimeError(f"cutter failed for cue {i}: {result.stderr[-200:]}")
        out_paths.append(out_path)
        logger.info("[cutter] Cut cue %d: %.2f–%.2f → %s", i, start, start + dur, os.path.basename(out_path))

    return out_paths


def cue_start_times_from_durations(durations: list[float]) -> list[float]:
    """Compute cumulative start times from a list of durations."""
    starts = []
    t = 0.0
    for d in durations:
        starts.append(t)
        t += d
    return starts
