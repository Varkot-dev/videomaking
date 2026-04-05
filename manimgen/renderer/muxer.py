# Audio-video muxer — combines a silent video with a narration audio track.
# Handles duration mismatches by either speeding up the video (audio longer)
# or padding the audio with silence (video longer).

import json
import logging
import os
import subprocess

logger = logging.getLogger(__name__)

_MISMATCH_WARN_THRESHOLD = 0.30  # warn if durations differ by more than 30%


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
      - If video is longer than audio: pad the audio with silence to match video duration.

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

    if audio_dur > video_dur:
        # Audio is longer → speed up video to match audio duration.
        speed_factor = video_dur / audio_dur  # < 1 means faster
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
        # Video is longer (or equal) → pad audio with silence to match video.
        audio_filter = "apad"
        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", audio_path,
            "-filter:a", audio_filter,
            "-c:v", "libx264",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]

    subprocess.run(cmd, check=True, capture_output=True)
    return output_path
