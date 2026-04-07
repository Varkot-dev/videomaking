# Assembler — concatenates muxed cue clips into a final video.
#
# Clip structure after cue-based generation:
#   section_01_cue00.mp4, section_01_cue01.mp4, section_01_cue02.mp4,
#   section_02_cue00.mp4, ...
#
# Rules:
#   - Cue clips within a section are joined with a hard cut (no transition).
#     A xfade between cue clips would look wrong — they are continuous narration.
#   - Section boundaries get a short crossfade (0.3s) to smooth the visual jump.
#   - All clips are normalised to 1920x1080 30fps yuv420p before joining.
#   - If only one clip total, it is moved to the output path directly.
#
# Section boundary detection:
#   Clips whose filename contains "_cue00" begin a new section.
#   Any clip that doesn't follow the cue naming scheme is treated as a
#   section boundary (legacy single-scene clips from TTS-off mode).

import os
import re
import subprocess

from manimgen import paths

_XFADE_DURATION = 0.3
_CUE00_PATTERN = re.compile(r"_cue00\.mp4$")


def assemble_video(video_paths: list[str], title: str) -> str:
    """Concatenate cue clips into the final video.

    Applies hard cuts between cues within a section and crossfades between
    section boundaries.

    Args:
        video_paths: Ordered list of muxed clip paths.
        title:       Used to name the output file.

    Returns:
        Path to the assembled final .mp4.
    """
    videos_dir = paths.videos_dir()
    os.makedirs(videos_dir, exist_ok=True)

    safe_title = title.lower().replace(" ", "_").replace("/", "-")
    output_path = os.path.join(videos_dir, f"{safe_title}.mp4")

    if not video_paths:
        raise ValueError("assemble_video called with no clips")

    if len(video_paths) == 1:
        os.replace(video_paths[0], output_path)
        return output_path

    # Step 1: normalise all clips to the same resolution/fps/format
    norm_paths = _normalise_all(video_paths, videos_dir)

    # Step 2: identify section boundaries (clips starting a new section)
    boundaries = _section_boundaries(video_paths)

    # Step 3: concatenate with hard cuts within sections, xfades between sections
    result = _concat(norm_paths, boundaries, videos_dir)

    os.replace(result, output_path)

    # Clean up normalised intermediates
    for p in norm_paths:
        if os.path.exists(p):
            os.remove(p)

    return output_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_all(paths: list[str], work_dir: str) -> list[str]:
    """Re-encode all clips to 1920x1080 30fps yuv420p aac."""
    norm_paths = []
    for i, path in enumerate(paths):
        norm = os.path.join(work_dir, f"_norm_{i:03d}.mp4")
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", path,
                "-vf", "scale=1920:1080,fps=30,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast",
                "-c:a", "aac", "-ar", "48000",
                norm,
            ],
            check=True,
            capture_output=True,
        )
        norm_paths.append(norm)
    return norm_paths


def _section_boundaries(paths: list[str]) -> set[int]:
    """Return the indices of clips that start a new section.

    Index 0 is always a boundary. Any clip whose basename matches
    `_cue00.mp4` also starts a new section. Non-cue clips each start
    a new section on their own.
    """
    boundaries: set[int] = {0}
    for i, path in enumerate(paths):
        name = os.path.basename(path)
        if _CUE00_PATTERN.search(name):
            boundaries.add(i)
        elif "_cue" not in name:
            # Legacy non-cue clip — treat as its own section
            boundaries.add(i)
    return boundaries


def _concat(
    norm_paths: list[str],
    boundaries: set[int],
    work_dir: str,
) -> str:
    """Join clips: hard cut within sections, xfade at boundaries."""
    if len(norm_paths) == 1:
        return norm_paths[0]

    # Group clips into sections
    sections: list[list[str]] = []
    current: list[str] = []
    for i, path in enumerate(norm_paths):
        if i in boundaries and current:
            sections.append(current)
            current = []
        current.append(path)
    if current:
        sections.append(current)

    # Within each section: hard-cut concat via filter_complex
    section_clips: list[str] = []
    for s_idx, section in enumerate(sections):
        if len(section) == 1:
            section_clips.append(section[0])
        else:
            merged = os.path.join(work_dir, f"_section_{s_idx:03d}.mp4")
            _hard_concat(section, merged)
            section_clips.append(merged)

    # Between sections: xfade
    if len(section_clips) == 1:
        return section_clips[0]

    result = section_clips[0]
    for i in range(1, len(section_clips)):
        tmp = os.path.join(work_dir, f"_xfade_{i:03d}.mp4")
        _xfade_pair(result, section_clips[i], tmp)
        # Clean up intermediates (but not the normalised originals — caller does that)
        if result not in norm_paths and os.path.exists(result):
            os.remove(result)
        result = tmp

    # Clean up per-section merged clips
    for s_idx in range(len(sections)):
        p = os.path.join(work_dir, f"_section_{s_idx:03d}.mp4")
        if os.path.exists(p) and p != result:
            os.remove(p)

    return result


def _hard_concat(paths: list[str], output_path: str) -> None:
    """Concatenate clips with hard cuts using ffmpeg concat filter."""
    inputs = []
    for p in paths:
        inputs += ["-i", p]

    n = len(paths)
    filter_str = "".join(f"[{i}:v][{i}:a]" for i in range(n))
    filter_str += f"concat=n={n}:v=1:a=1[v][a]"

    subprocess.run(
        [
            "ffmpeg", "-y",
            *inputs,
            "-filter_complex", filter_str,
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-c:a", "aac", "-ar", "48000",
            output_path,
        ],
        check=True,
        capture_output=True,
    )


def _video_duration(path: str) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ],
        check=True, capture_output=True, text=True,
    )
    return max(0.1, float(result.stdout.strip()))


def _xfade_pair(a: str, b: str, out: str) -> None:
    a_dur = _video_duration(a)
    offset = max(0.0, a_dur - _XFADE_DURATION)
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-i", a, "-i", b,
            "-filter_complex",
            (
                f"[0:v][1:v]xfade=transition=fade:"
                f"duration={_XFADE_DURATION}:offset={offset}[v];"
                f"[0:a][1:a]acrossfade=d={_XFADE_DURATION}[a]"
            ),
            "-map", "[v]", "-map", "[a]",
            "-c:v", "libx264", "-c:a", "aac", "-ar", "48000",
            out,
        ],
        check=True,
        capture_output=True,
    )
