"""
Extract gold-standard reference frames from 3Blue1Brown YouTube videos.

Downloads each video at 720p (sufficient for frame extraction), then pulls
one frame every 4 seconds, skipping the first 45s and last 60s of each video
(avoids intros, outros, pi creature segments, and chapter title cards).

Frames are saved to /tmp/3b1b_candidates/<video_id>/frame_NNNN.png
Review them manually and copy the keepers to:
  manimgen/manimgen/manimgen/reference_frames/

Target videos — chosen for visual variety covering the scene types we generate:
  1. Gradient descent (graphs, 3D loss surface, equations)
  2. But what is a neural network? (clean graphs, equations)
  3. Essence of linear algebra ch1 (vectors, axes, 2D)
  4. But what is the Fourier Transform? (graphs, waves, equations)
"""

import os
import subprocess
import sys
import tempfile
import shutil

VIDEOS = [
    {
        "id": "IHZwWFHWa-w",
        "title": "gradient_descent",
        "url": "https://www.youtube.com/watch?v=IHZwWFHWa-w",
        "skip_start": 45,
        "skip_end": 60,
    },
    {
        "id": "fNk_zzaMoSs",
        "title": "linear_algebra_ch1",
        "url": "https://www.youtube.com/watch?v=fNk_zzaMoSs",
        "skip_start": 30,
        "skip_end": 45,
    },
    {
        "id": "spUNpyF58BY",
        "title": "fourier_transform",
        "url": "https://www.youtube.com/watch?v=spUNpyF58BY",
        "skip_start": 45,
        "skip_end": 60,
    },
]

OUTPUT_DIR = "/tmp/3b1b_candidates"
FRAME_INTERVAL = 4      # extract one frame every N seconds
# Keep frames where mean brightness is between 15 and 180.
# < 15  → pure black (transition, fade to black)
# > 180 → photo thumbnail, chapter title card, or bright slide
# Valid 3B1B math frames (dark bg + colored elements) land in range ~20–120.
_MIN_MEAN = 8    # below this: pure black transition frame
_MAX_MEAN = 180  # above this: photo thumbnail or bright title card


def get_video_duration(video_path: str) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True, check=True
    )
    return float(result.stdout.strip())


def extract_frames(video_path: str, out_dir: str, skip_start: int, skip_end: int):
    """Extract one frame every FRAME_INTERVAL seconds, skipping start/end.

    Uses individual ffmpeg -ss calls rather than the select filter — the select
    filter resets t=0 after a seek and produces only one frame.
    """
    os.makedirs(out_dir, exist_ok=True)
    duration = get_video_duration(video_path)
    end_time = duration - skip_end

    if end_time <= skip_start:
        print(f"  WARNING: video too short after trimming, skipping")
        return 0

    timestamps = list(range(skip_start, int(end_time), FRAME_INTERVAL))
    extracted = 0
    for i, ts in enumerate(timestamps):
        out_path = os.path.join(out_dir, f"frame_{i:04d}.png")
        cmd = [
            "ffmpeg", "-y",
            "-ss", str(ts),
            "-i", video_path,
            "-frames:v", "1",
            "-q:v", "2",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=15)
        if result.returncode == 0 and os.path.exists(out_path):
            extracted += 1

    print(f"  Extracted {extracted} raw frames")
    return extracted


def filter_bad_frames(out_dir: str):
    """Remove pure black frames and bright photo/title-card frames.

    Valid 3B1B math frames have mean brightness ~20–120 (dark bg + colored elements).
    Pure black transitions fall below 15. Photo thumbnails and title cards exceed 180.
    """
    try:
        from PIL import Image, ImageStat
    except ImportError:
        print("  PIL not available — skipping frame filter")
        return

    frames = sorted(f for f in os.listdir(out_dir) if f.endswith(".png"))
    removed = 0
    for fname in frames:
        path = os.path.join(out_dir, fname)
        try:
            img = Image.open(path).convert("RGB")
            stat = ImageStat.Stat(img)
            mean = sum(stat.mean) / 3
            if mean < _MIN_MEAN or mean > _MAX_MEAN:
                os.remove(path)
                removed += 1
        except Exception as e:
            print(f"  Could not check {fname}: {e}")

    remaining = len([f for f in os.listdir(out_dir) if f.endswith(".png")])
    print(f"  Removed {removed} bad frames → {remaining} candidates remain")


def download_video(video: dict, work_dir: str) -> str | None:
    """Download video at 720p to work_dir. Returns path to downloaded file."""
    out_template = os.path.join(work_dir, "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
        "--merge-output-format", "mp4",
        "-o", out_template,
        "--no-playlist",
        "--quiet", "--progress",
        video["url"],
    ]
    print(f"\n[{video['title']}] Downloading {video['url']} ...")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        print(f"  ERROR: yt-dlp failed for {video['url']}")
        return None

    # Find the downloaded file
    for f in os.listdir(work_dir):
        if f.startswith(video["id"]) and f.endswith(".mp4"):
            return os.path.join(work_dir, f)

    print(f"  ERROR: could not find downloaded file in {work_dir}")
    return None


def main():
    print(f"Output: {OUTPUT_DIR}")
    print(f"Frame interval: every {FRAME_INTERVAL}s")
    print(f"Videos: {len(VIDEOS)}")
    print()

    total_frames = 0

    for video in VIDEOS:
        out_dir = os.path.join(OUTPUT_DIR, video["title"])
        os.makedirs(out_dir, exist_ok=True)

        # Check if already extracted
        existing = [f for f in os.listdir(out_dir) if f.endswith(".png")]
        if existing:
            print(f"[{video['title']}] Already have {len(existing)} frames, skipping download")
            total_frames += len(existing)
            continue

        with tempfile.TemporaryDirectory() as work_dir:
            video_path = download_video(video, work_dir)
            if not video_path:
                continue

            print(f"  Downloaded: {os.path.basename(video_path)}")
            n = extract_frames(video_path, out_dir, video["skip_start"], video["skip_end"])
            if n > 0:
                filter_bad_frames(out_dir)
                remaining = len([f for f in os.listdir(out_dir) if f.endswith(".png")])
                total_frames += remaining
                print(f"  [{video['title']}] Done — {remaining} frames in {out_dir}")

    print(f"\nDone. Total candidate frames: {total_frames}")
    print(f"\nReview frames in: {OUTPUT_DIR}")
    print("Then copy keepers to: manimgen/manimgen/manimgen/reference_frames/")
    print("\nTip: open with 'open /tmp/3b1b_candidates' on macOS")


if __name__ == "__main__":
    main()
