import os
import subprocess
import time
from datetime import datetime

from manimgen import paths
from manimgen.validator.codeguard import precheck_and_autofix_file
from manimgen.validator.env import get_render_env
from manimgen.validator.render_command import build_manimgl_command
from manimgen.validator.scene_ast_gate import inspect_scene_file

# Slack subtracted from "now" when computing the freshness floor for
# _find_rendered_video. Some filesystems store mtime at whole-second
# granularity, so a video written milliseconds after the render started can
# report an mtime marginally *before* it. One second of slack absorbs that
# without letting a genuinely stale previous-attempt render through.
_RENDER_FLOOR_SLACK_SECONDS = 1.0


def _render_floor() -> float:
    """Timestamp floor marking the start of a render.

    Passed to ``_find_rendered_video(newer_than=...)`` so a video produced by
    an *earlier* attempt can never be mistaken for this render's output.
    """
    return time.time() - _RENDER_FLOOR_SLACK_SECONDS


def _is_3d_scene(scene_path: str) -> bool:
    with open(scene_path) as f:
        return "ThreeDScene" in f.read()


def validate_scene_inputs(scene_path: str) -> dict:
    """Pre-render asset validation gate.

    Returns {"ok": bool, "errors": [str], "warnings": [str]}.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not os.path.exists(scene_path):
        errors.append(f"Scene file not found: {scene_path}")
        return {"ok": False, "errors": errors, "warnings": warnings}

    if os.path.getsize(scene_path) == 0:
        errors.append(f"Scene file is empty: {scene_path}")

    try:
        with open(scene_path) as f:
            f.read(1)
    except OSError as e:
        errors.append(f"Scene file not readable: {e}")

    scenes_dir = paths.scenes_dir()
    try:
        os.makedirs(scenes_dir, exist_ok=True)
        write_check = os.path.join(scenes_dir, ".write_check")
        with open(write_check, "w") as f:
            f.write("")
        os.unlink(write_check)
    except OSError as e:
        errors.append(f"Output scenes dir not writable: {e}")

    try:
        os.makedirs(paths.logs_dir(), exist_ok=True)
    except OSError as e:
        warnings.append(f"Could not create logs dir: {e}")

    return {"ok": len(errors) == 0, "errors": errors, "warnings": warnings}


def run_scene(scene_path: str, class_name: str) -> tuple[bool, str | None]:
    """
    Run a ManimGL scene file and return (success, video_path).
    Logs the attempt to output/logs/.
    """
    preflight = validate_scene_inputs(scene_path)
    if not preflight["ok"]:
        import logging as _logging

        _logging.getLogger(__name__).error(
            "[runner] Pre-render validation failed: %s", preflight["errors"]
        )
        return False, None

    logs_dir = paths.logs_dir()
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"{class_name}_{timestamp}.log")

    precheck = precheck_and_autofix_file(scene_path)
    if not precheck["ok"]:
        with open(log_path, "w") as f:
            if precheck.get("applied_fixes"):
                f.write("=== PRECHECK AUTO-FIXES ===\n")
                for fix in precheck.get("applied_fixes"):
                    f.write(f"- {fix}\n")
                f.write("\n")
            if precheck.get("layout_warnings"):
                f.write("=== PRECHECK LAYOUT WARNINGS ===\n")
                for warning in precheck["layout_warnings"]:
                    f.write(f"- {warning}\n")
                f.write("\n")
            f.write("=== PRECHECK ERROR ===\n")
            f.write(precheck["stderr"])
            f.write("\n")
        return False, None

    # Pre-execution AST gate (#27): manimgl imports the scene module, running
    # every top-level statement before the Scene is instantiated. Inspect the
    # exact file we are about to execute for disallowed top-level statements
    # (shelling out, importing os/subprocess, exec/eval, second class, ...).
    #
    # Warning-only by design: codeguard's banned-pattern denylist already
    # blocks the known exec/eval/shell primitives outright, and hard-breaking
    # here would regress any scene that emits a benign extra top-level
    # statement. We log findings loudly so they are visible in the run log.
    # TODO(#27): once Director output is constrained enough that findings are
    # reliably malicious, hard-block here (return False, None) instead of only
    # warning.
    gate = inspect_scene_file(scene_path)
    if not gate.ok:
        import logging as _logging

        _logging.getLogger(__name__).warning(
            "[runner] AST gate findings for %s (warning-only, render continues): %s",
            class_name,
            "; ".join(gate.findings),
        )

    # Director scenes can be long (multiple cue beats in one section file).
    # Use a larger timeout to avoid false "runtime" failures.
    timeout = 360 if _is_3d_scene(scene_path) else 240

    # Freshness floor for _find_rendered_video: any .mp4 older than this cannot
    # be the output of the render we are about to start. Backdated by one second
    # to absorb filesystem mtime granularity (some filesystems truncate to whole
    # seconds, which would otherwise reject a video this render just wrote).
    render_started_at = _render_floor()

    try:
        result = subprocess.run(
            build_manimgl_command(scene_path, class_name),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=get_render_env(),
        )

        with open(log_path, "w") as f:
            if precheck.get("applied_fixes"):
                f.write("=== PRECHECK AUTO-FIXES ===\n")
                for fix in precheck.get("applied_fixes"):
                    f.write(f"- {fix}\n")
                f.write("\n")
            if precheck.get("layout_warnings"):
                f.write("=== PRECHECK LAYOUT WARNINGS ===\n")
                for warning in precheck["layout_warnings"]:
                    f.write(f"- {warning}\n")
                f.write("\n")
            f.write(f"=== STDOUT ===\n{result.stdout}\n")
            f.write(f"=== STDERR ===\n{result.stderr}\n")
            f.write(f"=== RETURN CODE ===\n{result.returncode}\n")

        if result.returncode == 0:
            video_path = _find_rendered_video(class_name, newer_than=render_started_at)
            return True, video_path

        return False, None

    except subprocess.TimeoutExpired:
        with open(log_path, "w") as f:
            f.write(f"=== TIMEOUT ===\nScene rendering exceeded {timeout} seconds.\n")
        return False, None


def _find_rendered_video(
    class_name: str, newer_than: float | None = None
) -> str | None:
    """Search common ManimGL output directories for the rendered video.

    Selection is deliberately strict, because a wrong answer here is expensive:
    a retry that picks up a *previous* attempt's video will validate and ship
    the pre-fix render, so you pay an LLM for a fix that never reached the
    output and nothing downstream notices.

    Three rules, in order:

    1. **Exact stem preferred.** Plain substring matching (``class_name in f``)
       made "Section01Scene" match "Section01SceneOld.mp4". Files whose stem
       equals ``class_name`` win outright; looser substring matches are only a
       last resort.
    2. **Newest first.** ``os.walk`` order is arbitrary, so with several
       candidates the returned file was effectively random. Candidates are
       sorted by mtime descending.
    3. **Freshness floor.** ``newer_than`` (a POSIX timestamp, normally the
       time the render started) rejects any file that predates the current
       render — such a file cannot be this render's output.

    ``newer_than=None`` keeps the old permissive behaviour for callers that
    legitimately want a pre-existing render (the cache / --resume path in
    ``cli.py``, which does its own ``.hash`` sidecar freshness check).
    """
    from manimgen import paths as _paths

    # ManimGL writes to "videos/" relative to the scene file's directory.
    # Also check the configured output videos dir in case of prior pipeline runs.
    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(here))
    search_dirs = [
        os.path.join(project_root, "videos"),
        os.path.join(project_root, "media", "videos"),
        "videos",
        "media/videos",
        _paths.videos_dir(),
    ]

    exact: list[tuple[float, str]] = []
    partial: list[tuple[float, str]] = []
    seen: set[str] = set()

    for d in search_dirs:
        if not os.path.isdir(d):
            continue
        for root, _, files in os.walk(d):
            for f in files:
                if not f.endswith(".mp4") or class_name not in f:
                    continue
                path = os.path.join(root, f)
                try:
                    real = os.path.realpath(path)
                    if real in seen:
                        continue  # search dirs overlap; don't double-count
                    mtime = os.path.getmtime(path)
                except OSError:
                    continue
                seen.add(real)

                # Reject anything that predates the current render outright.
                if newer_than is not None and mtime < newer_than:
                    import logging as _logging

                    _logging.getLogger(__name__).debug(
                        "[runner] Ignoring stale render %s (mtime %.0f < floor %.0f)",
                        path,
                        mtime,
                        newer_than,
                    )
                    continue

                if os.path.splitext(f)[0] == class_name:
                    exact.append((mtime, path))
                else:
                    partial.append((mtime, path))

    for candidates in (exact, partial):
        if candidates:
            candidates.sort(key=lambda item: item[0], reverse=True)
            return candidates[0][1]
    return None
