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

from manimgen.utils import safe_probe_duration

logger = logging.getLogger(__name__)

# Mismatches larger than this are logged as warnings — indicates a cue
# placement or TTS/render timing problem worth investigating.
_WARN_THRESHOLD_SECONDS = 1.0

# The pipeline's dark background (matches the -c "#1C1C1C" render flag). Used to
# synthesize a placeholder clip when a cue's render produced no video stream.
_BG_COLOR = "#1C1C1C"
_SYNTH_RESOLUTION = "1920x1080"
_SYNTH_FPS = 60

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
            video_dur,
            audio_dur,
            diff,
        )
        _mismatch_log.append(
            {
                "output_path": output_path,
                "video_dur": video_dur,
                "audio_dur": audio_dur,
                "diff": diff,
            }
        )
    if diff > 1.5:
        logger.warning(
            "[muxer] LARGE MISMATCH (%.3fs) — last %.1fs of this cue will be a freeze-frame. "
            "Director likely miscalculated loop timing. Check loop wait() calls in the scene.",
            diff,
            diff,
        )

    # Graceful degradation: a too-short render can yield an .mp4 with NO video
    # stream. The freeze path's [0:v]tpad filter then errors ("matches no streams")
    # and the whole section gets dropped -> black gap. Instead, synthesize a solid
    # background for the full narration so the cue keeps its audio over a clean
    # frame. (Doesn't fix WHY the video is empty — that's the Director/timing layer
    # — but stops one empty cue from cascading into a dropped section.)
    if not _has_video_stream(video_path):
        logger.warning(
            "[muxer] %s has no video stream — synthesizing %s background for the "
            "%.2fs narration (degraded; section preserved).",
            video_path, _BG_COLOR, audio_dur,
        )
        _mux_synth_background(audio_path, output_path, audio_dur)
        return output_path

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
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-filter_complex",
        f"[1:a]apad=whole_dur={video_dur:.6f}[a]",
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{video_dur:.6f}",
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
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-i",
        audio_path,
        "-filter_complex",
        # tpad stop_duration = gap only, not total audio duration
        f"[0:v]tpad=stop_mode=clone:stop_duration={pad_secs:.6f}[v]",
        "-map",
        "[v]",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-t",
        f"{audio_dur:.6f}",
        output_path,
    ]
    _run(cmd, output_path)


def _mux_synth_background(
    audio_path: str,
    output_path: str,
    audio_dur: float,
) -> None:
    """Mux narration over a synthesized solid #1C1C1C background.

    Used when the cue's rendered video has no usable video stream (a too-short
    render). Produces a clean dark clip lasting the full narration so the section
    is preserved with its audio, rather than crashing the freeze path.
    """
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"color=c={_BG_COLOR}:s={_SYNTH_RESOLUTION}:r={_SYNTH_FPS}:d={audio_dur:.6f}",
        "-i",
        audio_path,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        "-t",
        f"{audio_dur:.6f}",
        output_path,
    ]
    _run(cmd, output_path)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _has_video_stream(path: str) -> bool:
    """Return True iff ``path`` contains at least one video stream.

    Fail-closed: any ffprobe error / missing binary / unexpected output is treated
    as "no usable video stream" so the caller degrades gracefully rather than
    feeding an empty file into the [0:v] freeze filter (which errors out).
    """
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "video" in result.stdout


def _get_duration(path: str) -> float:
    """Return media duration in seconds via ffprobe (always > 0).

    Some containers report no format-level duration ("N/A" or absent),
    which previously crashed muxing via ``float("N/A")``. Parsing is now
    delegated to ``utils.safe_probe_duration``; an unreadable duration
    falls back to the 0.1s floor (mirroring ``assembler._video_duration``)
    so muxing never dies on missing metadata.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install FFmpeg:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg"
        )
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = None
    dur = safe_probe_duration(data)
    if dur is None:
        logging.getLogger(__name__).warning(
            "[muxer] No readable duration for %s — using 0.1s floor", path
        )
        dur = 0.1
    return max(0.1, dur)


# A single mux/cut should finish in seconds. Cap at 300s so a wedged ffmpeg
# (e.g. a malformed filtergraph that never completes) is reaped instead of
# hanging the whole pipeline. Mirrors assembler's 300s subprocess timeouts.
_FFMPEG_TIMEOUT_SECONDS = 300


def _run(cmd: list[str], output_path: str) -> None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"[muxer] ffmpeg timed out after {_FFMPEG_TIMEOUT_SECONDS}s "
            f"for {output_path}"
        )
    if result.returncode != 0:
        raise RuntimeError(f"[muxer] ffmpeg failed for {output_path}:\n{result.stderr}")


# ---------------------------------------------------------------------------
# Video cutter (absorbed from cutter.py)
# ---------------------------------------------------------------------------

_MAX_PARALLEL_CUTS = min(4, (os.cpu_count() or 1))


def _cut_one(video_path: str, start: float, dur: float, out_path: str, i: int) -> str:
    from manimgen import paths as _paths

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        video_path,
        "-ss",
        f"{start:.6f}",
        "-t",
        f"{dur:.6f}",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(_paths.render_fps()),
        "-an",
        out_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_FFMPEG_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        logger.error(
            "[cutter] FFmpeg timed out after %ds for cue %d",
            _FFMPEG_TIMEOUT_SECONDS,
            i,
        )
        raise RuntimeError(
            f"cutter timed out after {_FFMPEG_TIMEOUT_SECONDS}s for cue {i}"
        )
    if result.returncode != 0:
        logger.error("[cutter] FFmpeg failed for cue %d: %s", i, result.stderr[-500:])
        raise RuntimeError(f"cutter failed for cue {i}: {result.stderr[-200:]}")
    logger.info(
        "[cutter] Cut cue %d: %.2f–%.2f → %s",
        i,
        start,
        start + dur,
        os.path.basename(out_path),
    )
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
