import os
import subprocess


def assemble_video(video_paths: list[str], title: str) -> str:
    """Concatenate rendered scene videos into a single final video using FFmpeg.

    Uses short crossfades and normalizes resolution/fps for robust stitching.
    """
    videos_dir = "manimgen/output/videos"
    os.makedirs(videos_dir, exist_ok=True)

    safe_title = title.lower().replace(" ", "_").replace("/", "-")
    output_path = os.path.join(videos_dir, f"{safe_title}.mp4")

    if len(video_paths) == 1:
        os.rename(video_paths[0], output_path)
        return output_path

    norm_paths: list[str] = []
    for idx, path in enumerate(video_paths):
        norm_path = os.path.join(videos_dir, f"norm_{idx:02d}.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", path,
                "-vf", "scale=1920:1080,fps=30,format=yuv420p",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-c:a", "aac",
                "-ar", "48000",
                norm_path,
            ],
            check=True,
            capture_output=True,
        )
        norm_paths.append(norm_path)

    if len(norm_paths) == 2:
        _xfade_pair(norm_paths[0], norm_paths[1], output_path)
    else:
        cur = norm_paths[0]
        for i in range(1, len(norm_paths)):
            tmp_out = os.path.join(videos_dir, f"xfade_tmp_{i:02d}.mp4")
            _xfade_pair(cur, norm_paths[i], tmp_out)
            if cur not in norm_paths and os.path.exists(cur):
                os.remove(cur)
            cur = tmp_out
        os.replace(cur, output_path)

    for p in norm_paths:
        if os.path.exists(p):
            os.remove(p)

    return output_path


def _video_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return max(0.1, float(result.stdout.strip()))


def _xfade_pair(a_path: str, b_path: str, out_path: str, fade_dur: float = 0.3) -> None:
    a_dur = _video_duration(a_path)
    offset = max(0.0, a_dur - fade_dur)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", a_path,
            "-i", b_path,
            "-filter_complex",
            (
                f"[0:v][1:v]xfade=transition=fade:duration={fade_dur}:offset={offset}[v];"
                f"[0:a][1:a]acrossfade=d={fade_dur}[a]"
            ),
            "-map", "[v]",
            "-map", "[a]",
            "-c:v", "libx264",
            "-c:a", "aac",
            out_path,
        ],
        check=True,
        capture_output=True,
    )
