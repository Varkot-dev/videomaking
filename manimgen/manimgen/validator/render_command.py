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
