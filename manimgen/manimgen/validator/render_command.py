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
