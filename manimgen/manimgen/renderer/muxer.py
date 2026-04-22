# Audio-video muxer — overlays a narration audio track onto a silent video clip.
#
# Design principle: NEVER warp playback speed to fix duration mismatches.
# Speed-warping was the original cause of the A/V sync problems this pipeline
# is designed to fix. With audio-first cuing, the video clip is generated to
# match the audio duration exactly, so mismatches should be small (<200ms).
#
# When a small mismatch does occur (stream-copy frame alignment, render timing):
#   - Audio longer than video: pad video with a freeze-frame on the last frame.
#   - Video longer than audio: pad audio with silence.
#
# Both strategies keep the narration at natural speed. The viewer will not
# notice a 100ms freeze or silence at the end of a clip.

import json
import logging
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

logger = logging.getLogger(__name__)

# Mismatches larger than this are logged as warnings — indicates a cue
# placement or TTS/render timing problem worth investigating.
_WARN_THRESHOLD_SECONDS = 1.0

# Module-level mismatch log — each entry is a dict with video_path, diff, cue info.
# The CLI reads this at the end of a run and prints a summary.
# Call clear_mismatch_log() at the start of each pipeline run.
_mismatch_log: list[dict] = []


def clear_mismatch_log() -> None:
    """Reset the mismatch log. Call at the start of each pipeline run."""
    global _mismatch_log
    _mismatch_log = []


def get_mismatch_log() -> list[dict]:
    """Return the accumulated mismatch log entries for this pipeline run."""
    return list(_mismatch_log)


def mux_audio_video(video_path: str, audio_path: str, output_path: str) -> str:
    """Overlay audio onto video, padding whichever is shorter.

    Duration mismatches are handled by padding — never by speed-warping.

    Args:
        video_path:  Path to the silent rendered video (.mp4).
        audio_path:  Path to the narration audio slice (.mp3).
        output_path: Destination path for the muxed .mp4.

    Returns:
        output_path on success.

    Raises:
        RuntimeError if ffmpeg/ffprobe is not installed or the mux fails.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    video_dur = _get_duration(video_path)
    audio_dur = _get_duration(audio_path)

    diff = abs(video_dur - audio_dur)
    if diff > _WARN_THRESHOLD_SECONDS:
        logger.warning(
            "[muxer] Duration mismatch: video=%.3fs audio=%.3fs diff=%.3fs — "
            "check cue placement or scene render timing.",
            video_dur, audio_dur, diff,
        )
        _mismatch_log.append({
            "output_path": output_path,
            "video_dur": video_dur,
            "audio_dur": audio_dur,
            "diff": diff,
        })
    if diff > 1.5:
        logger.warning(
            "[muxer] LARGE MISMATCH (%.3fs) — last %.1fs of this cue will be a freeze-frame. "
            "Director likely miscalculated loop timing. Check loop wait() calls in the scene.",
            diff, diff,
        )

    if audio_dur > video_dur:
        _mux_freeze_video(video_path, audio_path, output_path, audio_dur)
    else:
        _mux_pad_audio(video_path, audio_path, output_path, video_dur)

    return output_path


# ---------------------------------------------------------------------------
# Strategy implementations
# ---------------------------------------------------------------------------

def _mux_pad_audio(
    video_path: str,
    audio_path: str,
    output_path: str,
    video_dur: float,
) -> None:
    """Mux video + audio, padding audio with silence to match video duration."""
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex",
        f"[1:a]apad=whole_dur={video_dur:.6f}[a]",
        "-map", "0:v",
        "-map", "[a]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-t", f"{video_dur:.6f}",
        output_path,
    ]
    _run(cmd, output_path)


def _mux_freeze_video(
    video_path: str,
    audio_path: str,
    output_path: str,
    audio_dur: float,
) -> None:
    """Mux video + audio, freezing the last video frame to match audio duration."""
    video_dur = _get_duration(video_path)
    pad_secs = max(0.0, audio_dur - video_dur)
    cmd = [
        "ffmpeg", "-y",
        "-i", video_path,
        "-i", audio_path,
        "-filter_complex",
        # tpad stop_duration = gap only, not total audio duration
        f"[0:v]tpad=stop_mode=clone:stop_duration={pad_secs:.6f}[v]",
        "-map", "[v]",
        "-map", "1:a",
        "-c:v", "libx264",
        "-c:a", "aac",
        "-t", f"{audio_dur:.6f}",
        output_path,
    ]
    _run(cmd, output_path)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _get_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install FFmpeg:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg"
        )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def _run(cmd: list[str], output_path: str) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"[muxer] ffmpeg failed for {output_path}:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Video cutter (absorbed from cutter.py)
# ---------------------------------------------------------------------------

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
    """Cut a section video into per-cue clips using parallel FFmpeg re-encodes."""
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
            results[i] = future.result()

    return [results[i] for i in range(len(jobs))]


def cue_start_times_from_durations(durations: list[float]) -> list[float]:
    """Compute cumulative start times from a list of durations."""
    starts = []
    t = 0.0
    for d in durations:
        starts.append(t)
        t += d
    return starts
