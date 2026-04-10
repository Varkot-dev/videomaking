"""
Video cutter — slices a single rendered section video into per-cue clips
at exact timestamps derived from the TTS segmenter.

Used by the single-scene-per-section architecture: the Director generates
one .py file per section, ManimGL renders one .mp4, and this module cuts it
into N cue clips that the muxer then overlays with audio slices.
"""
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

_MAX_PARALLEL_CUTS = min(4, (os.cpu_count() or 1))


def _cut_one(video_path: str, start: float, dur: float, out_path: str, i: int) -> str:
    from manimgen import paths as _paths
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-ss", f"{start:.6f}",
        "-t", f"{dur:.6f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "18",
        "-pix_fmt", "yuv420p",
        "-r", str(_paths.render_fps()),
        "-an",
        out_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("[cutter] FFmpeg failed for cue %d: %s", i, result.stderr[-500:])
        raise RuntimeError(f"cutter failed for cue {i}: {result.stderr[-200:]}")
    logger.info("[cutter] Cut cue %d: %.2f–%.2f → %s", i, start, start + dur, os.path.basename(out_path))
    return out_path


def cut_video_at_cues(
    video_path: str,
    cue_start_times: list[float],
    cue_durations: list[float],
    output_dir: str,
    section_id: str,
) -> list[str]:
    """Cut a section video into per-cue clips using parallel FFmpeg re-encodes.

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
    jobs = [
        (i, start, dur, os.path.join(output_dir, f"{section_id}_cue{i:02d}_video.mp4"))
        for i, (start, dur) in enumerate(zip(cue_start_times, cue_durations))
    ]

    results: dict[int, str] = {}
    with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_CUTS) as pool:
        futures = {
            pool.submit(_cut_one, video_path, start, dur, out_path, i): i
            for i, start, dur, out_path in jobs
        }
        for future in as_completed(futures):
            i = futures[future]
            results[i] = future.result()  # raises on error, propagating to caller

    return [results[i] for i in range(len(jobs))]


def cue_start_times_from_durations(durations: list[float]) -> list[float]:
    """Compute cumulative start times from a list of durations."""
    starts = []
    t = 0.0
    for d in durations:
        starts.append(t)
        t += d
    return starts
