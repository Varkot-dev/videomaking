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
#     All crossfades are built in ONE ffmpeg call (chained xfade/acrossfade), so
#     the video is encoded once instead of once per section (#75). If that call
#     fails, the old pairwise path is tried; if it times out, or the pairwise
#     path also fails, sections are joined with hard cuts (stream copy) and a
#     warning is logged, so a slow encode never aborts assembly at the last step.
#   - All clips are normalised to the configured resolution and fps (yuv420p)
#     before joining.
#   - If only one clip total, it is copied to the output path directly.
#   - Intermediates live in a private temp folder that is removed on any exit,
#     and the final file is published with an atomic replace.
#
# Section boundary detection:
#   Clips whose filename contains "_cue00" begin a new section.
#   Any clip that doesn't follow the cue naming scheme is treated as a
#   section boundary (legacy single-scene clips from TTS-off mode).

import logging
import os
import re
import shutil
import subprocess
import tempfile
import time

from manimgen import paths
from manimgen.utils import ffmpeg_concat_line, replace_with_retry

_XFADE_DURATION = 0.3
_CUE00_PATTERN = re.compile(r"_cue00\.mp4$")

# Audio-stream probe budgets. This is a metadata-only ffprobe (no decoding), so
# it should return near-instantly; 10s is already generous. The reap budget is
# how long we wait for a killed child to actually die before giving up.
_AUDIO_PROBE_TIMEOUT_SECONDS = 10
_AUDIO_PROBE_REAP_TIMEOUT_SECONDS = 5

# Crossfade encode budget, scaled by how much video the call encodes. Audit
# measurement (#75): libx264 preset slow at 1080p60 ran at about 0.4x realtime
# on a 4-core sandbox, i.e. about 2.3s of wall time per second of output. The
# per-second rate leaves headroom for slower machines; the base covers ffmpeg
# start-up and short videos. A 6 minute video gets 300 + 360 * 4 = 1740s.
_XFADE_TIMEOUT_BASE_SECONDS = 300
_XFADE_TIMEOUT_PER_VIDEO_SECOND = 4.0


def _vf_scale() -> str:
    """Build the FFmpeg -vf scale filter string from config."""
    res = paths.render_resolution()  # e.g. "1920x1080"
    w, h = res.replace("x", ":").split(":")
    fps = paths.render_fps()
    return f"scale={w}:{h},fps={fps},format=yuv420p"


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

    # Replace every character Windows forbids in a filename (not just "/"): an
    # LLM title like "Binary Search: Halving" would otherwise write to an NTFS
    # alternate data stream named after the colon instead of a visible .mp4.
    # Trailing dots are also invalid on Windows.
    safe_title = re.sub(r'[<>:"/\\|?*]', "-", title.lower().replace(" ", "_"))
    safe_title = safe_title.rstrip(".") or "video"
    output_path = os.path.join(videos_dir, f"{safe_title}.mp4")

    if not video_paths:
        raise ValueError("assemble_video called with no clips")

    # All intermediates live in a private folder next to the output (same
    # volume, so the final move is an atomic rename) that is deleted on any
    # exit, including a failed or timed-out ffmpeg (#76). ignore_cleanup_errors
    # keeps Windows from raising if a handle is still draining.
    with tempfile.TemporaryDirectory(
        dir=videos_dir, prefix="_asm_", ignore_cleanup_errors=True
    ) as work_dir:
        if len(video_paths) == 1:
            # Copy, not move: the muxed clip stays in its cache folder.
            result = os.path.join(work_dir, "_single.mp4")
            shutil.copyfile(video_paths[0], result)
        else:
            # Step 1: normalise all clips to the same resolution/fps/format
            norm_paths = _normalise_all(video_paths, work_dir)

            # Step 2: identify section boundaries (clips starting a new section)
            boundaries = _section_boundaries(video_paths)

            # Step 3: hard cuts within sections, xfades between sections
            result = _concat(norm_paths, boundaries, work_dir)

        return _publish(result, output_path)


def _publish(src: str, output_path: str) -> str:
    """Move the finished video into place; return the path actually written.

    On Windows, replacing a file that a player has open raises PermissionError.
    Retry briefly, then fall back to a timestamped name so the finished render
    is never lost behind a locked file.
    """
    try:
        replace_with_retry(src, output_path)
        return output_path
    except PermissionError:
        stem, ext = os.path.splitext(output_path)
        fallback = f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
        n = 1
        while os.path.exists(fallback):
            n += 1
            fallback = f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}_{n}{ext}"
        logging.getLogger(__name__).warning(
            "[assembler] %s is in use (open in a player?); saving the new video "
            "as %s instead.",
            output_path,
            fallback,
        )
        replace_with_retry(src, fallback)
        return fallback


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalise_all(clip_paths: list[str], work_dir: str) -> list[str]:
    """Re-encode all clips to the configured resolution/fps, yuv420p, aac."""
    norm_paths = []
    for i, path in enumerate(clip_paths):
        norm = os.path.join(work_dir, f"_norm_{i:03d}.mp4")
        if _has_audio_stream(path):
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-vf",
                _vf_scale(),
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "17",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                norm,
            ]
        else:
            # Some fallback/error paths can leave us with video-only clips.
            # Inject silent audio so concat/xfade filters always have [v][a].
            dur = _video_duration(path)
            cmd = [
                "ffmpeg",
                "-y",
                "-i",
                path,
                "-f",
                "lavfi",
                "-t",
                str(dur),
                "-i",
                "anullsrc=r=48000:cl=stereo",
                "-vf",
                _vf_scale(),
                "-c:v",
                "libx264",
                "-preset",
                "slow",
                "-crf",
                "17",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-shortest",
                norm,
            ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
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

    result = os.path.join(work_dir, "_xfade_all.mp4")
    _crossfade_sections(section_clips, result, work_dir)
    return result


def _xfade_timeout(total_seconds: float) -> float:
    """ffmpeg timeout for a crossfade encode producing `total_seconds` of video."""
    return _XFADE_TIMEOUT_BASE_SECONDS + _XFADE_TIMEOUT_PER_VIDEO_SECOND * max(
        0.0, total_seconds
    )


def _boundary_fade(prev_dur: float, next_dur: float) -> float:
    """Crossfade length for one section boundary.

    A fixed 0.3s fade on a clip at or under the fade window (for example one
    whose duration hit the 0.1s probe floor) would dissolve that whole clip.
    Shrink the fade to half the shorter neighbour instead.
    """
    shortest = min(prev_dur, next_dur)
    if shortest <= _XFADE_DURATION:
        return max(0.01, shortest / 2.0)
    return _XFADE_DURATION


def _crossfade_sections(section_clips: list[str], out: str, work_dir: str) -> None:
    """Crossfade all sections into `out`, degrading instead of crashing.

    Order of attempts:
      1. One ffmpeg call that chains every xfade/acrossfade (single encode).
      2. On a non-timeout ffmpeg error: the pairwise path (one encode per
         boundary, each pair falling back to a hard cut on its own).
      3. On a timeout: hard cuts for every boundary (stream copy, no encode).
         Retrying pairwise after a timeout would only take longer.
    """
    log = logging.getLogger(__name__)
    try:
        _xfade_chain(section_clips, out)
        return
    except subprocess.TimeoutExpired as exc:
        log.warning(
            "[assembler] Crossfade encode timed out after %.0fs; joining the %d "
            "sections with hard cuts instead.",
            exc.timeout or 0.0,
            len(section_clips),
        )
    except (subprocess.CalledProcessError, OSError) as exc:
        log.warning(
            "[assembler] Single-pass crossfade failed (%s); retrying one "
            "section boundary at a time.",
            exc,
        )
        try:
            _xfade_pairwise(section_clips, out, work_dir)
            return
        except (subprocess.SubprocessError, OSError) as exc2:
            log.warning(
                "[assembler] Pairwise crossfade failed too (%s); joining the "
                "sections with hard cuts instead.",
                exc2,
            )
    _hard_concat(section_clips, out)


def _xfade_chain(section_clips: list[str], out: str) -> None:
    """Join all sections with crossfades in a single ffmpeg encode.

    Builds a chained filtergraph:
      [0:v][1:v]xfade[v1]; [v1][2:v]xfade[v2]; ...
      [0:a][1:a]acrossfade[a1]; [a1][2:a]acrossfade[a2]; ...
    Each xfade offset is the length of the video built so far minus that
    boundary's fade, so every section is decoded and encoded exactly once.
    """
    durations = [_video_duration(p) for p in section_clips]
    v_parts: list[str] = []
    a_parts: list[str] = []
    v_prev, a_prev = "[0:v]", "[0:a]"
    built = durations[0]
    for k in range(1, len(section_clips)):
        fade = _boundary_fade(durations[k - 1], durations[k])
        offset = max(0.0, built - fade)
        last = k == len(section_clips) - 1
        v_out = "[v]" if last else f"[v{k}]"
        a_out = "[a]" if last else f"[a{k}]"
        v_parts.append(
            f"{v_prev}[{k}:v]xfade=transition=fade:"
            f"duration={fade:.6f}:offset={offset:.6f}{v_out}"
        )
        a_parts.append(f"{a_prev}[{k}:a]acrossfade=d={fade:.6f}{a_out}")
        v_prev, a_prev = v_out, a_out
        built += durations[k] - fade

    cmd = ["ffmpeg", "-y"]
    for p in section_clips:
        cmd += ["-i", p]
    cmd += [
        "-filter_complex",
        ";".join(v_parts + a_parts),
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "17",
        "-c:a",
        "aac",
        "-ar",
        "48000",
        out,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=_xfade_timeout(built))


def _xfade_pairwise(section_clips: list[str], out: str, work_dir: str) -> None:
    """Fallback: fold sections left to right, one crossfade encode per boundary.

    Slower than `_xfade_chain` (the growing video is re-encoded at every
    boundary) but isolates a boundary that ffmpeg cannot crossfade: that pair
    alone falls back to a hard cut.
    """
    result = section_clips[0]
    for i in range(1, len(section_clips)):
        tmp = os.path.join(work_dir, f"_xfade_{i:03d}.mp4")
        try:
            _xfade_pair(result, section_clips[i], tmp)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            logging.getLogger(__name__).warning(
                "[assembler] Crossfade into section %d failed; using a hard cut.",
                i + 1,
            )
            _hard_concat([result, section_clips[i]], tmp)
        if result not in section_clips and os.path.exists(result):
            os.remove(result)
        result = tmp
    replace_with_retry(result, out)


def _hard_concat(paths: list[str], output_path: str) -> None:
    """Concatenate normalised clips with hard cuts using stream copy.

    All inputs are already normalised (same codec, resolution, fps, sample rate)
    so stream copy is lossless and near-instant. A concat demuxer list file is
    used rather than the filter_complex concat filter to avoid re-encoding.
    """

    list_file = output_path + ".concat_list.txt"
    try:
        with open(list_file, "w", encoding="utf-8") as f:
            for p in paths:
                f.write(ffmpeg_concat_line(p))
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                list_file,
                "-c",
                "copy",
                output_path,
            ],
            check=True,
            capture_output=True,
            timeout=300,
        )
    finally:
        if os.path.exists(list_file):
            os.remove(list_file)


def _video_duration(path: str) -> float:
    """Best-effort clip duration in seconds (always > 0).

    ffprobe returns 'N/A' (or empty) for some re-encoded/short clips when
    the container has no format-level duration. Previously `float('N/A')`
    crashed the whole assembly. Try format duration, then stream duration,
    then fall back to the 0.1s floor — assembly must never die because one
    intermediate clip lacks duration metadata.
    """

    def _probe(entries: str) -> float | None:
        try:
            result = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    entries,
                    "-of",
                    "default=noprint_wrappers=1:nokey=1",
                    path,
                ],
                check=True,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except (subprocess.SubprocessError, OSError):
            return None
        raw = result.stdout.strip().splitlines()
        for line in raw:
            line = line.strip()
            if not line or line == "N/A":
                continue
            try:
                return float(line)
            except ValueError:
                continue
        return None

    dur = _probe("format=duration")
    if dur is None:
        dur = _probe("stream=duration")
    if dur is None:
        logging.getLogger(__name__).warning(
            "[assembler] No readable duration for %s — using 0.1s floor", path
        )
        dur = 0.1
    return max(0.1, dur)


def _has_audio_stream(path: str) -> bool:
    """Return True iff `path` contains at least one audio stream.

    Fails safe to False on ANY probe failure (non-zero exit, timeout, or
    exception). Rationale, applied uniformly to every failure mode: the
    no-audio branch in `_normalise_all` injects a silent anullsrc track, which
    always yields a valid [v][a] clip. Assuming audio *present* when it is not
    makes ffmpeg's `-c:a aac` map fail outright (there is no stream to encode),
    aborting assembly. A wrong "no audio" guess costs a silent track on a clip
    that had one; a wrong "has audio" guess costs the entire render.

    The probe child is always reaped — this runs once per clip, so a leaked
    ffprobe per failure accumulates across a full assembly.
    """
    proc = None
    try:
        proc = subprocess.Popen(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                path,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        out, _ = proc.communicate(timeout=_AUDIO_PROBE_TIMEOUT_SECONDS)
        if proc.returncode != 0:
            # Fail safe to False — see docstring. Previously returned True,
            # which contradicted the reasoning documented directly below and
            # could abort assembly on a transient probe failure.
            import logging

            logging.getLogger(__name__).warning(
                "[assembler] Audio stream probe exited %d for %s — assuming NO "
                "audio (silent track will be injected)",
                proc.returncode,
                os.path.basename(path),
            )
            return False
        return out.strip() != ""
    except Exception as exc:
        # Fail safe to False: the no-audio branch in _normalise_all injects a
        # silent anullsrc track, which always yields a valid [v][a] clip.
        # Assuming audio *present* when it is not makes ffmpeg's `-c:a aac`
        # map fail outright (there is no stream to encode), aborting assembly.
        # Log so transient probe failures stay observable.
        import logging

        logging.getLogger(__name__).warning(
            "[assembler] Audio stream probe failed for %s: %s — assuming NO audio "
            "(silent track will be injected)",
            os.path.basename(path),
            exc,
        )
        return False
    finally:
        # communicate(timeout=...) does NOT kill the child on TimeoutExpired —
        # it just stops waiting. Without this the ffprobe keeps running and
        # then lingers as a zombie, once per clip.
        if proc is not None and proc.poll() is None:
            proc.kill()
            try:
                proc.communicate(timeout=_AUDIO_PROBE_REAP_TIMEOUT_SECONDS)
            except Exception:
                # Last resort: at minimum reap the PID so it is not a zombie.
                try:
                    proc.wait(timeout=_AUDIO_PROBE_REAP_TIMEOUT_SECONDS)
                except Exception:
                    pass


def _xfade_pair(a: str, b: str, out: str) -> None:
    a_dur = _video_duration(a)
    # If the first clip is at/under the xfade window (e.g. _video_duration hit
    # its 0.1s floor on unreadable metadata), a fixed 0.3s xfade would force
    # offset=0 and cross-dissolve the entire first section from frame one.
    # Shrink the transition to half the available duration so the offset stays
    # positive and the first section is mostly shown before the dissolve.
    xfade_dur = _XFADE_DURATION
    if a_dur <= _XFADE_DURATION:
        xfade_dur = max(0.01, a_dur / 2.0)
        logging.getLogger(__name__).warning(
            "[assembler] First clip %s is only %.3fs (<= %.2fs xfade window) — "
            "shrinking transition to %.3fs to avoid dissolving the whole section.",
            os.path.basename(a),
            a_dur,
            _XFADE_DURATION,
            xfade_dur,
        )
    offset = max(0.0, a_dur - xfade_dur)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            a,
            "-i",
            b,
            "-filter_complex",
            (
                f"[0:v][1:v]xfade=transition=fade:"
                f"duration={xfade_dur}:offset={offset}[v];"
                f"[0:a][1:a]acrossfade=d={xfade_dur}[a]"
            ),
            "-map",
            "[v]",
            "-map",
            "[a]",
            "-c:v",
            "libx264",
            "-preset",
            "slow",
            "-crf",
            "17",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            out,
        ],
        check=True,
        capture_output=True,
        timeout=_xfade_timeout(a_dur + _video_duration(b)),
    )
