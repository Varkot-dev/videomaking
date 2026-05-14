"""
Extract frames at section boundaries from a manimgen-rendered video.

Defect-clustering happens at section transitions: opening titles stack at
the first second, residual mobjects from the prior section linger into the
next. Sampling at uniform 10s intervals will miss these. This script reads
plan.json, computes cumulative section start times, and extracts frames at:
  - opening:  start + 0.5s and start + 2.0s (catches title-zone collisions)
  - middle:   section midpoint
  - closing:  end - 0.5s (catches residual mobjects, missing FadeOut)

Plus 3 bookend frames: video start (0.5s, 1.5s) and video end (duration - 0.5s).

Section start times come from per-section TTS audio durations recorded in the
muxed clip filenames. If those aren't available, falls back to the plan.json
section list and uses ffprobe on each muxed cue clip to compute durations.

Output:
  - JPEGs in /tmp/manimgen_review/frame_<TIMESTAMP>s.jpg
  - JSON metadata at /tmp/manimgen_review/frames.json mapping frame_path → {
      "section_id": int, "section_title": str, "phase": "open"|"mid"|"close",
      "timestamp_s": float
    }

Usage:
  python3 extract_section_frames.py <video_path> [--out /tmp/manimgen_review]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys


_DEFAULT_OUT = "/tmp/manimgen_review"
_PLAN_PATH = (
    "/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/output/plan.json"
)
_MUXED_DIR = (
    "/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/output/muxed"
)


def _ffprobe_duration(path: str) -> float:
    """Return media duration in seconds, or 0.0 on error."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, text=True, timeout=15,
        )
        return float(out.stdout.strip()) if out.returncode == 0 and out.stdout.strip() else 0.0
    except (subprocess.SubprocessError, ValueError):
        return 0.0


def _section_durations_from_muxed() -> list[float]:
    """Sum the muxed cue clip durations per section.

    The muxer produces section_NN_cueMM.mp4 files. Sum across cues per section
    to get section durations. Returns a list ordered by section number.
    """
    if not os.path.isdir(_MUXED_DIR):
        return []
    section_durs: dict[int, float] = {}
    for fname in os.listdir(_MUXED_DIR):
        if "_video" in fname or not fname.endswith(".mp4"):
            continue  # skip pre-mux video-only clips
        # Match section_05_cue02.mp4
        if not fname.startswith("section_"):
            continue
        try:
            sec_id = int(fname.split("_")[1])
        except (IndexError, ValueError):
            continue
        path = os.path.join(_MUXED_DIR, fname)
        section_durs[sec_id] = section_durs.get(sec_id, 0.0) + _ffprobe_duration(path)
    return [section_durs[k] for k in sorted(section_durs.keys())]


def _section_titles_from_plan() -> list[str]:
    if not os.path.isfile(_PLAN_PATH):
        return []
    try:
        with open(_PLAN_PATH) as f:
            plan = json.load(f)
    except (OSError, json.JSONDecodeError):
        return []
    return [s.get("title", f"Section {i+1}") for i, s in enumerate(plan.get("sections", []))]


def _extract_frame(video: str, ts: float, out_dir: str) -> str | None:
    out_path = os.path.join(out_dir, f"frame_{ts:.1f}s.jpg")
    proc = subprocess.run(
        ["ffmpeg", "-ss", f"{ts:.2f}", "-i", video, "-vframes", "1",
         "-q:v", "2", out_path, "-y"],
        capture_output=True, timeout=30,
    )
    return out_path if proc.returncode == 0 and os.path.exists(out_path) else None


def extract(video_path: str, out_dir: str) -> dict:
    if not os.path.isfile(video_path):
        return {"error": f"video not found: {video_path}", "frames": []}

    os.makedirs(out_dir, exist_ok=True)
    video_dur = _ffprobe_duration(video_path)
    if video_dur <= 0:
        return {"error": f"could not read duration: {video_path}", "frames": []}

    durations = _section_durations_from_muxed()
    titles = _section_titles_from_plan()

    frames: list[dict] = []

    # Bookend: video opening
    for ts in (0.5, 1.5):
        if ts < video_dur:
            path = _extract_frame(video_path, ts, out_dir)
            if path:
                frames.append({
                    "frame_path": path, "timestamp_s": ts,
                    "section_id": None, "section_title": None, "phase": "intro",
                })

    # Per-section frames
    cumulative = 0.0
    for i, dur in enumerate(durations):
        sec_id = i + 1
        title = titles[i] if i < len(titles) else f"Section {sec_id}"
        start = cumulative
        end = cumulative + dur
        mid = start + dur / 2.0
        # opening: 0.5s and 2.0s past start (if section is long enough)
        for offset, phase in ((0.5, "open"), (2.0, "open"), (None, "mid"), (None, "close")):
            if phase == "mid":
                ts = mid
            elif phase == "close":
                ts = max(start + 0.5, end - 0.5)
            else:
                ts = start + offset
            if ts >= video_dur or ts < 0:
                continue
            path = _extract_frame(video_path, ts, out_dir)
            if path:
                frames.append({
                    "frame_path": path, "timestamp_s": round(ts, 2),
                    "section_id": sec_id, "section_title": title, "phase": phase,
                })
        cumulative = end

    # Bookend: video closing
    for offset in (2.0, 0.5):
        ts = max(0.0, video_dur - offset)
        if ts > cumulative - 0.1:  # only if past last section's close
            path = _extract_frame(video_path, ts, out_dir)
            if path:
                frames.append({
                    "frame_path": path, "timestamp_s": round(ts, 2),
                    "section_id": None, "section_title": None, "phase": "outro",
                })

    metadata = {
        "video_path": video_path,
        "video_duration_s": round(video_dur, 2),
        "section_count": len(durations),
        "frames": frames,
    }
    meta_path = os.path.join(out_dir, "frames.json")
    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=2)
    return metadata


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("video_path")
    p.add_argument("--out", default=_DEFAULT_OUT)
    args = p.parse_args()

    result = extract(args.video_path, args.out)
    print(json.dumps(result, indent=2))
    return 0 if "error" not in result else 1


if __name__ == "__main__":
    raise SystemExit(main())
