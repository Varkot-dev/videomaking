"""Single source of truth for the manimgl render command.

Three call sites (runner, fallback, retry) used to each build their own
``["manimgl", ...]`` argv inline, which let them drift. They now share this
builder so a render-flag fix lands in exactly one place.

Why no ``--fps``: manimgl 1.7.2 declares ``--fps`` without ``type=int`` and
then assigns the raw string into ``camera_config.fps`` (see manimlib/config.py
``if args.fps: camera_config.fps = args.fps``). ``Scene.run`` later computes
``1 / self.camera.fps``, which raises ``TypeError: int / str`` and aborts EVERY
render. The flag is unusable in this build, so we omit it; manimgl renders at
its bundled default (30fps) and the assembler normalizes the final cut to the
configured fps. See docs/KNOWN_ISSUES.md.
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import NamedTuple

from manimgen import paths
from manimgen.probes import overlap_report
from manimgen.validator.scene_ast_gate import format_gate_error, inspect_scene_file

logger = logging.getLogger(__name__)

# The canonical dark background. CLAUDE.md: the flag is -c, NOT --background_color.
_BACKGROUND_COLOR = "#1C1C1C"


def build_manimgl_command(
    scene_path: str, class_name: str, draft: bool | None = None
) -> list[str]:
    """Return the argv for rendering ``class_name`` in ``scene_path`` to a file.

    ``draft=True`` renders at the draft size for this command only (see
    ``paths.render_size_flags``); None follows the process-wide draft switch.

    Deliberately omits ``--fps`` (broken in manimgl 1.7.2 — see module docstring).
    """
    return [
        "manimgl",
        scene_path,
        class_name,
        "-w",
        *paths.render_size_flags(draft),
        "-c",
        _BACKGROUND_COLOR,
    ]


def with_utf8_io(env: dict[str, str]) -> dict[str, str]:
    """Return ``env`` with UTF-8 mode forced on for the manimgl child.

    On Windows a Python child whose stdout is a pipe encodes it with the ANSI
    code page (cp1252), so any non-ASCII character manimgl or the scene prints
    raises ``UnicodeEncodeError`` mid-render, and its own ``open()`` calls read
    UTF-8 files as cp1252. ``PYTHONUTF8=1`` fixes both; the parent decodes the
    captured output as UTF-8 to match. ``setdefault`` keeps an explicit user
    override. A no-op in practice on Linux and macOS, where UTF-8 is the default.
    """
    env = dict(env)
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


class RenderResult(NamedTuple):
    """Outcome of one manimgl render.

    ``ok`` is True only when manimgl exited 0 AND a video newer than the start
    of the render was found; ``video_path`` is then set. On any failure
    ``stderr`` explains why (manimgl's own stderr, a timeout note, or the folders
    searched for a missing video).

    ``overlaps`` lists text drawn on top of other text, as seen by the
    render-side probe (#97, ``manimgen.probes``); ``probe_error`` is set when
    the probe itself failed. Neither ever turns a render into a failure here:
    callers decide what an overlap means.
    """

    ok: bool
    video_path: str | None
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    overlaps: tuple = ()
    probe_error: str | None = None


# Render seconds allowed per second of video, on top of the configured budget.
# Measured on software OpenGL (no GPU, Mesa llvmpipe): a 1080x1920 short drew
# 4 to 7 frames a second at 30 fps, about 5 to 8 render seconds per video
# second, and a 45 s scene overran the fixed 240 s budget although it was
# rendering normally. A GPU is far faster; this only raises the ceiling.
_RENDER_SECONDS_PER_VIDEO_SECOND = 10.0
_RENDER_STARTUP_SECONDS = 60.0


def _estimated_video_seconds(code: str) -> float:
    """Lower-bound length of the scene from its play/wait calls (0 if unknown)."""
    from manimgen.validator.timing_verifier import (
        _construct_body_source,
        _parse_cue_block,
        _time_for_statements,
    )

    body = _construct_body_source(code)
    tree = _parse_cue_block(body) if body else None
    if tree is None:
        return 0.0
    seconds, _unknown = _time_for_statements(tree.body)
    return seconds


def _scene_timeout(scene_path: str) -> float:
    """Render budget for this scene.

    The configured budget (3D scenes get the 3D budget), raised for long scenes
    so a render that is working is not killed for being long: a fixed limit
    killed valid 45 s shorts on a machine without a GPU.
    """
    try:
        with open(scene_path, encoding="utf-8") as f:
            code = f.read()
    except OSError:
        code = ""
    kind = "3d" if "ThreeDScene" in code else "2d"
    base = paths.render_timeout(kind)
    if os.environ.get(f"MANIMGEN_RENDER_TIMEOUT_{kind.upper()}"):
        return base  # a limit set explicitly for this run is used as given
    length = _estimated_video_seconds(code) if code else 0.0
    return max(
        base, _RENDER_STARTUP_SECONDS + _RENDER_SECONDS_PER_VIDEO_SECOND * length
    )


# A draft render is a quick look, so it gets a much shorter budget than the
# final render: a draft that cannot finish in this time is scored as failed.
# The 540x960 frame has a quarter of the pixels of 1080x1920.
_DRAFT_SECONDS_PER_VIDEO_SECOND = 4.0
_DRAFT_STARTUP_SECONDS = 45.0
_DRAFT_MIN_SECONDS = 90.0


def draft_scene_timeout(scene_path: str) -> float:
    """Render budget for a draft of this scene, never above the full budget."""
    try:
        with open(scene_path, encoding="utf-8") as f:
            code = f.read()
    except OSError:
        code = ""
    length = _estimated_video_seconds(code) if code else 0.0
    wanted = max(
        _DRAFT_MIN_SECONDS,
        _DRAFT_STARTUP_SECONDS + _DRAFT_SECONDS_PER_VIDEO_SECOND * length,
    )
    return min(wanted, _scene_timeout(scene_path))


def run_manimgl(
    scene_path: str,
    class_name: str,
    timeout: float | None = None,
    draft: bool | None = None,
) -> RenderResult:
    """Render ``class_name`` and return a structured result.

    The single entry point for every manimgl render (first pass, retry and
    fallback). ``timeout`` defaults to the configured 2D/3D budget; on expiry the
    whole process tree is killed. Exit 0 with no fresh video is a failure.
    ``draft=True`` renders at the draft size for this call alone (it never
    touches the process-wide draft switch, which parallel sections share).

    Hard safety block (#87): the exact file manimgl would execute is first run
    through ``scene_ast_gate``. A rejected (or unreadable) file is never handed
    to manimgl; the result is a failure whose ``stderr`` lists the findings, so
    the retry loop can ask for a fix. This is a static check, not a sandbox.
    """
    gate = inspect_scene_file(scene_path)
    if not gate.ok:
        message = format_gate_error(gate)
        logger.error(
            "[render] scene safety gate rejected %s (%s); not rendering: %s",
            scene_path,
            class_name,
            "; ".join(gate.findings),
        )
        return RenderResult(False, None, "", message, None, False)

    # Imported here: runner imports this module at load time.
    from manimgen import procutil
    from manimgen.validator.env import get_render_env
    from manimgen.validator.runner import (
        _find_rendered_video,
        _render_floor,
        _video_search_dirs,
    )

    if timeout is None:
        timeout = _scene_timeout(scene_path)

    # Freshness floor: a video older than this cannot be this render's output,
    # so a stale file from an earlier attempt is never mistaken for it.
    started_at = _render_floor()
    env = with_utf8_io(get_render_env())
    probe_dir = None
    report_path = None
    if overlap_report.probe_enabled():
        probe_dir = tempfile.mkdtemp(prefix="manimgen_probe_")
        report_path = str(Path(probe_dir) / "overlaps.json")
        env = overlap_report.with_probe_env(
            env, report_path, paths.short_safe_margins() if paths.is_short() else None
        )
    try:
        returncode, stdout, stderr, timed_out = procutil.run_tree(
            build_manimgl_command(scene_path, class_name, draft),
            timeout=timeout,
            env=env,
        )
        report = (
            overlap_report.read_report(report_path)
            if probe_dir
            else overlap_report.OverlapReport()
        )
        video = None
        if not timed_out and returncode == 0:
            video = _find_rendered_video(class_name, newer_than=started_at)
            if video is not None:
                # Without a report (probe off or silent) a stale sidecar from an
                # earlier render of this class is removed, never re-read.
                overlap_report.save_sidecar(video, report_path)
    finally:
        if probe_dir:
            shutil.rmtree(probe_dir, ignore_errors=True)
    if report.probe_error:
        logger.warning(
            "[render] overlap probe error for %s: %s", class_name, report.probe_error
        )
    if timed_out:
        note = (
            f"TimeoutExpired: scene rendering exceeded {timeout:g} seconds "
            "(process tree killed)."
        )
        return RenderResult(False, None, stdout, note, None, True)
    if returncode != 0:
        return RenderResult(False, None, stdout, stderr, returncode, False)

    if video is None:
        searched = ", ".join(_video_search_dirs())
        note = (
            f"manimgl exited 0 but no fresh video for {class_name} was found "
            f"(searched: {searched})."
        )
        return RenderResult(
            False, None, stdout, (stderr + "\n" + note).strip(), 0, False
        )
    if report.overlaps:
        logger.warning(
            "[render] %s: %d text placement defect(s) found by the render probe",
            class_name,
            len(report.overlaps),
        )
    return RenderResult(
        True,
        video,
        stdout,
        stderr,
        0,
        False,
        overlaps=report.overlaps,
        probe_error=report.probe_error,
    )
