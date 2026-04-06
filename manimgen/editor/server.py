"""
ManimGen Editor — lightweight browser-based clip editor.

Usage:
    manimgen-edit                   # load output/muxed if present, else output/videos
    manimgen-edit --videos path/    # load a specific folder of .mp4 files

Opens at http://localhost:5001
"""

import argparse
import json
import os
import subprocess
import webbrowser
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file

app = Flask(__name__)

# Resolved at startup
VIDEOS_DIR: Path = Path("manimgen/output/videos")
OUTPUT_DIR: Path = Path("manimgen/output/videos/exports")


def _default_videos_dir() -> Path:
    """Prefer manimgen/output/muxed when it exists; otherwise manimgen/output/videos."""
    muxed = Path("manimgen/output/muxed")
    videos = Path("manimgen/output/videos")
    if muxed.exists():
        return muxed.resolve()
    return videos.resolve()


def _get_clips() -> list[dict]:
    """Scan VIDEOS_DIR for .mp4 files and return metadata list."""
    clips = []
    for p in sorted(VIDEOS_DIR.glob("*.mp4")):
        # Skip temp files and assembled output files
        if p.stem.endswith("_temp") or "_search" in p.stem:
            continue
        if p.stem.startswith("_tmp_"):
            continue
        duration = _probe_duration(p)
        clips.append({
            "id": p.stem,
            "filename": p.name,
            "path": str(p.resolve()),
            "duration": duration,
        })
    return clips


def _probe_duration(path: Path) -> float:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_entries", "format=duration",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        data = json.loads(result.stdout)
        return round(float(data["format"]["duration"]), 2)
    except Exception:
        return 0.0


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("editor.html")


@app.route("/api/clips")
def api_clips():
    return jsonify(_get_clips())


@app.route("/api/video/<filename>")
def api_video(filename):
    path = VIDEOS_DIR / filename
    if not path.exists() or not path.suffix == ".mp4":
        return "Not found", 404
    return send_file(str(path.resolve()), mimetype="video/mp4")


@app.route("/api/exports")
def api_exports():
    """List exported videos with their sizes and paths."""
    exports_dir = VIDEOS_DIR / "exports"
    if not exports_dir.exists():
        return jsonify([])
    exports = []
    for p in sorted(exports_dir.glob("*.mp4")):
        try:
            size = p.stat().st_size
        except Exception:
            size = 0
        exports.append({
            "filename": p.name,
            "path": str(p.resolve()),
            "size": size,
        })
    return jsonify(exports)


@app.route("/api/export", methods=["POST"])
def api_export():
    body = request.json
    clips = body.get("clips", [])   # [{filename, trim_start, trim_end}, ...]
    title = body.get("title", "final_video")

    if not clips:
        return jsonify({"error": "No clips provided"}), 400

    safe_title = title.lower().replace(" ", "_").replace("/", "-")

    # Exports go into a dedicated subdirectory so they don't appear as source clips
    exports_dir = VIDEOS_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    output_path = exports_dir / f"{safe_title}.mp4"

    # Use a unique run ID so concurrent exports don't collide on temp file names
    run_id = uuid4().hex[:8]
    list_path = VIDEOS_DIR / f"concat_list_{run_id}.txt"

    trimmed_paths = []
    try:
        for i, clip in enumerate(clips):
            src = VIDEOS_DIR / clip["filename"]
            if not src.exists():
                return jsonify({"error": f"File not found: {clip['filename']}"}), 400

            trim_start = float(clip.get("trim_start", 0))
            trim_end = float(clip.get("trim_end", 0))
            duration = float(clip.get("duration", 0))
            trim_end_actual = trim_end if trim_end > 0 else duration

            trimmed = VIDEOS_DIR / f"_tmp_{run_id}_{i}_{src.stem}.mp4"
            cmd = [
                "ffmpeg", "-y",
                "-ss", str(trim_start),
                "-i", str(src.resolve()),
                "-t", str(max(0.1, trim_end_actual - trim_start)),
                "-c:v", "libx264", "-c:a", "aac",
                "-preset", "fast",
                str(trimmed),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return jsonify({"error": f"Trim failed for {clip['filename']}", "details": result.stderr}), 500
            trimmed_paths.append(trimmed)

        # Write concat list
        with open(list_path, "w") as f:
            for tp in trimmed_paths:
                f.write(f"file '{tp.resolve()}'\n")

        # Concat
        concat_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0",
            "-i", str(list_path),
            "-c", "copy",
            str(output_path),
        ]
        result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return jsonify({"error": "Concat failed", "details": result.stderr}), 500

        return jsonify({
            "output": str(output_path),
            "filename": output_path.name,
            "export_dir": str(output_path.parent),
        })

    finally:
        # Clean up temp files regardless of success or failure
        for tp in trimmed_paths:
            try:
                tp.unlink()
            except Exception:
                pass
        if list_path.exists():
            try:
                list_path.unlink()
            except Exception:
                pass


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    global VIDEOS_DIR, OUTPUT_DIR

    parser = argparse.ArgumentParser(description="ManimGen Editor")
    parser.add_argument(
        "--videos",
        default=None,
        help="Directory of .mp4 clips (default: output/muxed if it exists, else output/videos)",
    )
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()

    VIDEOS_DIR = Path(args.videos).resolve() if args.videos else _default_videos_dir()
    OUTPUT_DIR = VIDEOS_DIR / "exports"

    if not VIDEOS_DIR.exists():
        print(f"[editor] Videos directory not found: {VIDEOS_DIR}")
        return

    # Clean up any leftover temp files and concat lists from a previous crashed export
    for tmp in VIDEOS_DIR.glob("_tmp_*.mp4"):
        try:
            tmp.unlink()
        except Exception:
            pass
    for concat_list in VIDEOS_DIR.glob("concat_list_*.txt"):
        try:
            concat_list.unlink()
        except Exception:
            pass
    # Also clean legacy concat_list.txt (pre-run-id naming)
    legacy_concat = VIDEOS_DIR / "concat_list.txt"
    if legacy_concat.exists():
        try:
            legacy_concat.unlink()
        except Exception:
            pass

    print(f"[editor] Loading clips from: {VIDEOS_DIR}")
    print(f"[editor] Exports will be saved to: {OUTPUT_DIR}")
    print(f"[editor] Opening http://localhost:{args.port}")
    webbrowser.open(f"http://localhost:{args.port}")
    app.run(port=args.port, debug=False)


if __name__ == "__main__":
    main()
