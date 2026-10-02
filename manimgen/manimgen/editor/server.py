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
import math
import os
import re
import secrets
import subprocess
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file

from manimgen import paths
from manimgen.utils import ffmpeg_concat_line, safe_probe_duration

# Mutating-export safety bounds.
_MAX_TITLE_LEN = 120
_DEFAULT_TITLE = "final_video"

# Hosts the editor answers to. Anything else (a DNS-rebinding page whose Host
# header is attacker-controlled) is refused before any route runs.
_ALLOWED_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_BIND_HOST = "127.0.0.1"

app = Flask(__name__)
logger = logging.getLogger(__name__)

# Resolved at startup
VIDEOS_DIR: Path = Path(paths.videos_dir())

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


def _host_allowed() -> bool:
    """True if the Host header names a loopback host (port ignored)."""
    try:
        hostname = urlsplit("//" + request.host).hostname
    except ValueError:
        return False
    return hostname in _ALLOWED_HOSTS


@app.before_request
def _guard_host():
    """Refuse non-loopback Host headers (blocks DNS rebinding) for every method."""
    if _host_allowed():
        return None
    logger.warning(
        "[editor] Rejected %s %s: bad Host header", request.method, request.path
    )
    return jsonify({"error": "Forbidden: unexpected Host header"}), 403


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
        duration = _cached_duration(p)
        clips.append(
            {
                "id": p.stem,
                "filename": p.name,
                "path": str(p.resolve()),
                "duration": duration,
            }
        )
    return clips


# (path, mtime_ns, size) -> duration. A changed file gets a new key, so a stale
# value is never served; failed probes (None) are not cached so they retry.
_DURATION_CACHE: dict[tuple[str, int, int], float] = {}


def _cached_duration(path: Path) -> float | None:
    """``_probe_duration`` memoized per unchanged file (one ffprobe per file)."""
    try:
        st = path.stat()
    except OSError:
        return _probe_duration(path)
    key = (str(path), st.st_mtime_ns, st.st_size)
    if key in _DURATION_CACHE:
        return _DURATION_CACHE[key]
    duration = _probe_duration(path)
    if duration is not None:
        _DURATION_CACHE[key] = duration
    return duration


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
            encoding="utf-8",
            errors="replace",
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


def _parse_seconds(value, name: str) -> float:
    """Parse a client-supplied seconds value: finite and non-negative, else ValueError."""
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a number")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} must be a number") from None
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be a finite number >= 0")
    return number


def _reserve_export_path(exports_dir: Path, safe_title: str) -> Path:
    """Atomically claim a free ``<title>.mp4`` (``<title>_2.mp4``, ...) name.

    The empty file is created exclusively, so two concurrent exports can never
    pick the same name and an existing export is never overwritten.
    """
    n = 1
    while True:
        name = f"{safe_title}.mp4" if n == 1 else f"{safe_title}_{n}.mp4"
        candidate = exports_dir / name
        try:
            with open(candidate, "xb"):
                pass
        except FileExistsError:
            n += 1
            continue
        return candidate


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
    if not isinstance(clips, list) or not all(isinstance(c, dict) for c in clips):
        return jsonify({"error": "clips must be a list of objects"}), 400

    # Validate every clip up front so a bad value is a clean 400 JSON error
    # before any ffmpeg work (bare float() gave 500s, and nan/inf/negative
    # values reached ffmpeg).
    plan = []  # (src, trim_start, seconds or None for "to the end")
    for clip in clips:
        filename = clip.get("filename")
        if not isinstance(filename, str):
            return jsonify({"error": "Each clip needs a string filename"}), 400
        src = _safe_under(VIDEOS_DIR, filename)
        if src is None or src.suffix != ".mp4":
            return jsonify({"error": f"Invalid filename: {filename}"}), 400
        if not src.exists():
            return jsonify({"error": f"File not found: {filename}"}), 400
        try:
            trim_start = _parse_seconds(clip.get("trim_start", 0), "trim_start")
            trim_end = _parse_seconds(clip.get("trim_end", 0), "trim_end")
            duration = _parse_seconds(clip.get("duration", 0), "duration")
        except ValueError as exc:
            return jsonify({"error": f"{filename}: {exc}"}), 400
        trim_end_actual = trim_end if trim_end > 0 else duration
        if trim_end_actual > 0 and trim_end_actual <= trim_start:
            return jsonify(
                {"error": f"{filename}: trim_end must be after trim_start"}
            ), 400
        seconds = (
            max(0.1, trim_end_actual - trim_start) if trim_end_actual > 0 else None
        )
        plan.append((src, trim_start, seconds))

    # Build a filesystem-safe output name: collapse anything outside [A-Za-z0-9_-]
    # (spaces, slashes, backslashes, null bytes, dots, unicode) to "_", cap the
    # length so a giant title can't blow the path limit, and fall back to a
    # default when the result is empty. The cap+slug also neutralizes traversal
    # ("../") and the null-byte truncation trick.
    safe_title = re.sub(r"[^\w\-]", "_", str(title))[:_MAX_TITLE_LEN] or _DEFAULT_TITLE

    # Exports go into a dedicated subdirectory so they don't appear as source clips
    exports_dir = VIDEOS_DIR / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    # Never overwrite an earlier export: claim the next free name.
    output_path = _reserve_export_path(exports_dir, safe_title)

    # Use a unique run ID so concurrent exports don't collide on temp file names
    run_id = uuid4().hex[:8]
    list_path = VIDEOS_DIR / f"concat_list_{run_id}.txt"
    # Encode to a temp file, then atomically move it onto the reserved name, so
    # a failed or interrupted export leaves no truncated file in exports/.
    partial_path = VIDEOS_DIR / f"_tmp_{run_id}_export.mp4"
    published = False

    trimmed_paths = []
    try:
        for i, (src, trim_start, seconds) in enumerate(plan):
            trimmed = VIDEOS_DIR / f"_tmp_{run_id}_{i}_{src.stem}.mp4"
            cmd = ["ffmpeg", "-y", "-ss", str(trim_start), "-i", str(src.resolve())]
            if seconds is not None:
                cmd += ["-t", str(seconds)]
            # Normalise pixel format and audio layout so the stream-copy
            # concat below never joins clips with mismatched parameters.
            cmd += [
                "-c:v",
                "libx264",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-preset",
                "fast",
                str(trimmed),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
            )
            if result.returncode != 0:
                return jsonify(
                    {
                        "error": f"Trim failed for {src.name}",
                        "details": result.stderr,
                    }
                ), 500
            trimmed_paths.append(trimmed)

        # Write concat list
        with open(list_path, "w", encoding="utf-8") as f:
            for tp in trimmed_paths:
                f.write(ffmpeg_concat_line(str(tp.resolve())))

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
            str(partial_path),
        ]
        result = subprocess.run(
            concat_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
        )
        if result.returncode != 0:
            return jsonify({"error": "Concat failed", "details": result.stderr}), 500

        os.replace(partial_path, output_path)
        published = True

        return jsonify(
            {
                "output": str(output_path),
                "filename": output_path.name,
                "export_dir": str(output_path.parent),
            }
        )

    finally:
        # Clean up temp files regardless of success or failure
        if not published:
            for leftover in (partial_path, output_path):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError as exc:
                    logger.warning(
                        "[editor] Could not remove %s: %s", leftover.name, exc
                    )
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
    global VIDEOS_DIR, EDITOR_TOKEN

    parser = argparse.ArgumentParser(description="ManimGen Editor")
    parser.add_argument(
        "--videos",
        default=None,
        help="Directory of .mp4 clips (default: output/muxed if it exists, else output/videos)",
    )
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()

    VIDEOS_DIR = Path(args.videos).resolve() if args.videos else _default_videos_dir()

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
    print(f"[editor] Exports will be saved to: {VIDEOS_DIR / 'exports'}")
    print(f"[editor] Opening http://localhost:{args.port}")
    webbrowser.open(f"http://localhost:{args.port}")
    app.run(host=_BIND_HOST, port=args.port, debug=False)


if __name__ == "__main__":
    main()
