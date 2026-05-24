"""
ManimGen Editor — lightweight browser-based clip editor.

Usage:
    manimgen-edit                   # load output/muxed if present, else output/videos
    manimgen-edit --videos path/    # load a specific folder of .mp4 files

Opens at http://localhost:5001
"""

import argparse
import json
import logging
import os
import re
import secrets
import subprocess
import webbrowser
from pathlib import Path
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file

from manimgen import paths
from manimgen.utils import safe_probe_duration

# Mutating-export safety bounds.
_MAX_TITLE_LEN = 120
_DEFAULT_TITLE = "final_video"

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Resolved at startup
VIDEOS_DIR: Path = Path(paths.videos_dir())
OUTPUT_DIR: Path = Path(paths.exports_dir())

# Shared-token guard. Read from env at startup; if unset, a random token is
# generated in main() and printed to the console. This is a localhost dev tool,
# so the *primary* gate for mutating requests is a same-origin check (the
# existing editor.html UI issues plain same-origin fetches and cannot be
# modified here). The token is an optional override for non-browser clients
# (curl, scripts) that cannot present a trusted Origin/Referer.
EDITOR_TOKEN: str = os.environ.get("MANIMGEN_EDITOR_TOKEN", "")


def _safe_under(base: Path, candidate: str) -> Path | None:
    """Resolve ``base / candidate`` and return it only if it stays under ``base``.

    Rejects path-traversal (``../``) escapes and any filename containing a
    single quote (``'``) — the latter would break the ffmpeg concat-list
    ``file '<path>'`` syntax, and rejecting is simpler and safer than escaping.
    Returns ``None`` when containment fails or the name is unsafe.
    """
    if "'" in candidate:
        return None
    base_resolved = base.resolve()
    try:
        resolved = (base_resolved / candidate).resolve()
    except (OSError, RuntimeError, ValueError):
        return None
    if not resolved.is_relative_to(base_resolved):
        return None
    return resolved


def _is_same_origin() -> bool:
    """True if the request carries an Origin/Referer matching this server's host.

    Browsers send ``Origin`` on POST requests; the editor.html UI is served from
    and posts back to the same host, so legitimate UI traffic always passes.
    """
    host = request.host_url.rstrip("/")
    origin = request.headers.get("Origin")
    if origin and origin.rstrip("/") == host:
        return True
    referer = request.headers.get("Referer")
    if referer and referer.startswith(host + "/"):
        return True
    return False


@app.before_request
def _guard_mutating_requests():
    """Light auth for state-changing methods only.

    GET/HEAD/OPTIONS (clip browsing, video streaming) stay open on localhost.
    Mutating methods (POST/PUT/PATCH/DELETE) must be same-origin OR present a
    matching ``X-Editor-Token`` header. This blocks drive-by/CSRF-style hits
    that trigger ffmpeg subprocesses without breaking the same-origin UI.
    """
    if request.method in ("GET", "HEAD", "OPTIONS"):
        return None
    token = request.headers.get("X-Editor-Token", "")
    if EDITOR_TOKEN and secrets.compare_digest(token, EDITOR_TOKEN):
        return None
    if _is_same_origin():
        return None
    logger.warning(
        "[editor] Rejected %s %s: not same-origin and no valid X-Editor-Token",
        request.method,
        request.path,
    )
    return jsonify({"error": "Forbidden: same-origin or X-Editor-Token required"}), 403


def _default_videos_dir() -> Path:
    """Prefer muxed dir when it exists; otherwise videos dir."""
    muxed = Path(paths.muxed_dir())
    videos = Path(paths.videos_dir())
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
        clips.append(
            {
                "id": p.stem,
                "filename": p.name,
                "path": str(p.resolve()),
                "duration": duration,
            }
        )
    return clips


def _probe_duration(path: Path) -> float | None:
    """Probe a clip's duration via ffprobe.

    Returns the duration in seconds, or ``None`` when ffprobe is missing,
    times out, fails, or emits an unreadable/``"N/A"`` duration. Parsing is
    delegated to ``utils.safe_probe_duration`` (the same tolerant parser the
    renderer uses) instead of a bare ``float(...)`` that silently collapses
    every failure to ``0.0`` — a real failure is now logged and surfaced as
    ``None`` so the UI can show "unknown" rather than a fabricated ``0.0s``.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_entries",
                "format=duration",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("[editor] ffprobe failed for %s: %s", path.name, exc)
        return None

    try:
        data = json.loads(result.stdout)
    except (ValueError, TypeError) as exc:
        logger.warning(
            "[editor] ffprobe returned unparseable output for %s: %s", path.name, exc
        )
        return None

    duration = safe_probe_duration(data)
    if duration is None:
        logger.warning("[editor] No usable duration for %s", path.name)
        return None
    return round(duration, 2)


# ── Routes ────────────────────────────────────────────────────────────────────


@app.route("/")
def index():
    return render_template("editor.html")


@app.route("/api/clips")
def api_clips():
    return jsonify(_get_clips())


@app.route("/api/video/<filename>")
def api_video(filename):
    safe_path = _safe_under(VIDEOS_DIR, filename)
    if safe_path is None:
        return "Forbidden", 403
    if not safe_path.exists() or safe_path.suffix != ".mp4":
        return "Not found", 404
    return send_file(str(safe_path), mimetype="video/mp4")


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
        except Exception as exc:
            logger.warning("[editor] Could not stat export file %s: %s", p.name, exc)
            size = 0
        exports.append(
            {
                "filename": p.name,
                "path": str(p.resolve()),
                "size": size,
            }
        )
    return jsonify(exports)


@app.route("/api/export", methods=["POST"])
def api_export():
    # request.json raises (415/AttributeError→500) when the Content-Type is
    # wrong/absent or the body is empty. Parse defensively and reject a
    # non-object body with a 400 instead of crashing.
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    clips = body.get("clips", [])  # [{filename, trim_start, trim_end}, ...]
    title = body.get("title", _DEFAULT_TITLE)

    if not clips:
        return jsonify({"error": "No clips provided"}), 400

    # Build a filesystem-safe output name: collapse anything outside [A-Za-z0-9_-]
    # (spaces, slashes, backslashes, null bytes, dots, unicode) to "_", cap the
    # length so a giant title can't blow the path limit, and fall back to a
    # default when the result is empty. The cap+slug also neutralizes traversal
    # ("../") and the null-byte truncation trick.
    safe_title = re.sub(r"[^\w\-]", "_", str(title))[:_MAX_TITLE_LEN] or _DEFAULT_TITLE

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
            filename = clip["filename"]
            src = _safe_under(VIDEOS_DIR, filename)
            if src is None or src.suffix != ".mp4":
                return jsonify({"error": f"Invalid filename: {filename}"}), 400
            if not src.exists():
                return jsonify({"error": f"File not found: {filename}"}), 400

            trim_start = float(clip.get("trim_start", 0))
            trim_end = float(clip.get("trim_end", 0))
            duration = float(clip.get("duration", 0))
            trim_end_actual = trim_end if trim_end > 0 else duration

            trimmed = VIDEOS_DIR / f"_tmp_{run_id}_{i}_{src.stem}.mp4"
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                str(trim_start),
                "-i",
                str(src.resolve()),
                "-t",
                str(max(0.1, trim_end_actual - trim_start)),
                "-c:v",
                "libx264",
                "-c:a",
                "aac",
                "-preset",
                "fast",
                str(trimmed),
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                return jsonify(
                    {
                        "error": f"Trim failed for {clip['filename']}",
                        "details": result.stderr,
                    }
                ), 500
            trimmed_paths.append(trimmed)

        # Write concat list
        with open(list_path, "w") as f:
            for tp in trimmed_paths:
                f.write(f"file '{tp.resolve()}'\n")

        # Concat
        concat_cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output_path),
        ]
        result = subprocess.run(concat_cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            return jsonify({"error": "Concat failed", "details": result.stderr}), 500

        return jsonify(
            {
                "output": str(output_path),
                "filename": output_path.name,
                "export_dir": str(output_path.parent),
            }
        )

    finally:
        # Clean up temp files regardless of success or failure
        for tp in trimmed_paths:
            try:
                tp.unlink()
            except Exception as exc:
                logger.warning(
                    "[editor] Could not remove temp clip %s: %s", tp.name, exc
                )
        if list_path.exists():
            try:
                list_path.unlink()
            except Exception as exc:
                logger.warning(
                    "[editor] Could not remove concat list %s: %s", list_path.name, exc
                )


# ── Entry point ───────────────────────────────────────────────────────────────


def main():
    global VIDEOS_DIR, OUTPUT_DIR, EDITOR_TOKEN

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
        except Exception as exc:
            logger.warning(
                "[editor] Startup cleanup: could not remove %s: %s", tmp.name, exc
            )
    for concat_list in VIDEOS_DIR.glob("concat_list_*.txt"):
        try:
            concat_list.unlink()
        except Exception as exc:
            logger.warning(
                "[editor] Startup cleanup: could not remove %s: %s",
                concat_list.name,
                exc,
            )
    # Also clean legacy concat_list.txt (pre-run-id naming)
    legacy_concat = VIDEOS_DIR / "concat_list.txt"
    if legacy_concat.exists():
        try:
            legacy_concat.unlink()
        except Exception as exc:
            logger.warning(
                "[editor] Startup cleanup: could not remove legacy concat list: %s", exc
            )

    # Shared-token guard: use env token if provided, else generate one so the
    # operator can authenticate non-browser clients (the same-origin UI works
    # without it). Mutating endpoints require same-origin OR this token.
    if not EDITOR_TOKEN:
        EDITOR_TOKEN = secrets.token_urlsafe(16)
    print(f"[editor] Editor token (X-Editor-Token header): {EDITOR_TOKEN}")
    print("[editor] Same-origin browser requests do not need the token.")

    print(f"[editor] Loading clips from: {VIDEOS_DIR}")
    print(f"[editor] Exports will be saved to: {OUTPUT_DIR}")
    print(f"[editor] Opening http://localhost:{args.port}")
    webbrowser.open(f"http://localhost:{args.port}")
    app.run(port=args.port, debug=False)


if __name__ == "__main__":
    main()
