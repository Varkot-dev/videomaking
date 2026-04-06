# Audio-video muxer — combines a silent video with a narration audio track.
# Handles duration mismatches by either speeding up/looping the video (audio longer)
# or trimming video (video longer).

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_MISMATCH_WARN_THRESHOLD = 0.30
_MISMATCH_LOOP_THRESHOLD = 2.0  # if audio is >2× video, loop video instead of speed-change


def _get_video_duration(video_path: str) -> float:
    """Return video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "json",
                video_path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install FFmpeg to enable muxing:\n"
            "  macOS:   brew install ffmpeg\n"
            "  Ubuntu:  sudo apt install ffmpeg\n"
            "  Windows: https://ffmpeg.org/download.html"
        )
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def mux_audio_video(video_path: str, audio_path: str, output_path: str) -> str:
    """Mux audio and video into a single .mp4 with voiceover.

    Synchronisation strategy:
      - If audio is longer than video: speed up the video to match audio duration.
      - If video is longer than audio: trim video to match audio duration.

    Always re-encodes so timing adjustments are applied correctly.
    Returns the path to the muxed output file.
    """
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    video_dur = _get_video_duration(video_path)

    # Import here to avoid circular imports; tts module is only needed for duration.
    from manimgen.renderer.tts import get_audio_duration
    audio_dur = get_audio_duration(audio_path)

    # Warn on large mismatches — something may be wrong with generation.
    if video_dur > 0:
        ratio = abs(video_dur - audio_dur) / video_dur
        if ratio > _MISMATCH_WARN_THRESHOLD:
            logger.warning(
                "[muxer] Large duration mismatch: video=%.1fs, audio=%.1fs (%.0f%% diff). "
                "Narration timing may be off.",
                video_dur, audio_dur, ratio * 100,
            )

    if audio_dur > video_dur and video_dur > 0 and (audio_dur / video_dur) > _MISMATCH_LOOP_THRESHOLD:
        # Extreme mismatch — loop the video instead of distorting playback speed.
        logger.info(
            "[muxer] Looping video (%.1fs) to match audio (%.1fs)",
            video_dur, audio_dur,
        )
        cmd = [
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", video_path,
            "-i", audio_path,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]
    elif audio_dur > video_dur:
        speed_factor = video_dur / audio_dur
        video_filter = f"setpts={speed_factor}*PTS"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter:v", video_filter,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]
    else:
        # Video is longer (or equal) → trim video to audio to avoid dead silent tails.
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-t", str(audio_dur),
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
