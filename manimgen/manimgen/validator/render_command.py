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

from typing import NamedTuple

from manimgen import paths

# The canonical dark background. CLAUDE.md: the flag is -c, NOT --background_color.
_BACKGROUND_COLOR = "#1C1C1C"


def build_manimgl_command(scene_path: str, class_name: str) -> list[str]:
    """Return the argv for rendering ``class_name`` in ``scene_path`` to a file.

    Deliberately omits ``--fps`` (broken in manimgl 1.7.2 — see module docstring).
    """
    return [
        "manimgl",
        scene_path,
        class_name,
        "-w",
        paths.render_quality_flag(),
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
    """

    ok: bool
    video_path: str | None
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool


def _scene_timeout(scene_path: str) -> float:
    """Configured render budget for this scene (3D scenes get the 3D budget)."""
    try:
        with open(scene_path, encoding="utf-8") as f:
            is_3d = "ThreeDScene" in f.read()
    except OSError:
        is_3d = False
    return paths.render_timeout("3d" if is_3d else "2d")


def run_manimgl(
    scene_path: str, class_name: str, timeout: float | None = None
) -> RenderResult:
    """Render ``class_name`` and return a structured result.

    The single entry point for every manimgl render (first pass, retry and
    fallback). ``timeout`` defaults to the configured 2D/3D budget; on expiry the
    whole process tree is killed. Exit 0 with no fresh video is a failure.
    """
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
    returncode, stdout, stderr, timed_out = procutil.run_tree(
        build_manimgl_command(scene_path, class_name),
        timeout=timeout,
        env=with_utf8_io(get_render_env()),
    )
    if timed_out:
        note = (
            f"TimeoutExpired: scene rendering exceeded {timeout:g} seconds "
            "(process tree killed)."
        )
        return RenderResult(False, None, stdout, note, None, True)
    if returncode != 0:
        return RenderResult(False, None, stdout, stderr, returncode, False)

    video = _find_rendered_video(class_name, newer_than=started_at)
    if video is None:
        searched = ", ".join(_video_search_dirs())
        note = (
            f"manimgl exited 0 but no fresh video for {class_name} was found "
            f"(searched: {searched})."
        )
        return RenderResult(
            False, None, stdout, (stderr + "\n" + note).strip(), 0, False
        )
    return RenderResult(True, video, stdout, stderr, 0, False)
