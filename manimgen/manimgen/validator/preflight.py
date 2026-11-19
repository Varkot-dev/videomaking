"""Preflight: find layout problems and crashes before the real render.

A real render draws every frame (about 6 minutes for a 43 s Short on software
OpenGL), and every fix of a layout problem used to cost another one. ManimGL can
execute a scene's ``construct`` without drawing frames: with ``-s`` (skip
animations) every ``play`` jumps to its end state and ``wait`` advances the
clock in one step. This module runs the scene that way, through the same
safety-gated entry point as a real render (``run_manimgl(dry_run=True)``), and
reports what the checks that look at positions see:

* the render probe's findings (text over text, off screen, outside the Shorts
  safe area, too small, text on a shape): the same ``Overlap`` objects and kinds
  a real render reports, because the probe samples after every ``play`` and
  ``wait``, which in skip mode are exactly the end states it samples anyway;
* the layout kit's findings (``ShortScene.note`` prints ``[scenekit] ...``);
* a crash, classified like a render failure (same stderr, same error types).

Not covered, because they need real frames: black or frozen frames, edge
clipping measured on pixels, the LLM vision check, and the length of the video.
The caller still runs those on the one real render that follows.

Skip mode runs updaters once per ``play``/``wait`` with the full ``dt`` instead
of once per frame, so a scene whose layout depends on many small steps of an
updater can end in a different state than a real render. ``scene.time`` still
advances by the full run time.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from manimgen import paths
from manimgen.probes import overlap_report
from manimgen.validator.render_command import run_manimgl

logger = logging.getLogger(__name__)

KIT_PREFIX = "[scenekit] "
KIT_ISSUE_PREFIX = "LAYOUT KIT:"


@dataclass(frozen=True)
class PreflightResult:
    """What a dry run of one scene found.

    ``ran`` is True when the scene executed to its end. ``crashed`` is True when
    manimgl exited non-zero (a real error in the scene, or the safety gate
    refusing it); ``stderr`` then holds the same text a failed render gives.
    ``timed_out`` means the dry run was killed at its own short budget: nothing
    is known about the scene, so the caller goes on to the real render.
    """

    ran: bool = False
    crashed: bool = False
    timed_out: bool = False
    stderr: str = ""
    returncode: int | None = None
    overlaps: tuple = ()
    kit_findings: tuple[str, ...] = ()
    probe_error: str | None = None
    seconds: float = 0.0

    @property
    def placement_issues(self) -> list[str]:
        """Hard issue lines for the visual-repair prompt (probe, then kit)."""
        return overlap_report.overlap_issues(self.overlaps) + [
            f"{KIT_ISSUE_PREFIX} {line}" for line in self.kit_findings
        ]

    @property
    def has_findings(self) -> bool:
        return bool(self.overlaps or self.kit_findings)

    @property
    def actionable(self) -> bool:
        """True when there is something to fix before rendering."""
        return self.crashed or self.has_findings

    @property
    def clean(self) -> bool:
        """The scene ran and nothing needs fixing."""
        return self.ran and not self.actionable

    @property
    def error_type(self):
        """The retry loop's ``SceneErrorType`` for a crash (None otherwise)."""
        if not self.crashed:
            return None
        from manimgen.validator.retry import _classify_error

        return _classify_error(self.stderr)

    def summary(self) -> str:
        if self.timed_out:
            return f"timed out after {self.seconds:.0f}s"
        if self.crashed:
            return f"crashed in {self.seconds:.1f}s"
        return (
            f"{len(self.overlaps)} probe finding(s), "
            f"{len(self.kit_findings)} kit finding(s) in {self.seconds:.1f}s"
        )


def kit_findings_from(stdout: str) -> tuple[str, ...]:
    """The ``[scenekit] ...`` lines a ShortScene printed, without the prefix."""
    found: list[str] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if line.startswith(KIT_PREFIX):
            text = line[len(KIT_PREFIX) :].strip()
            if text and text not in found:
                found.append(text)
    return tuple(found)


def preflight(scene_path: str, class_name: str) -> PreflightResult:
    """Dry-run ``class_name`` from ``scene_path`` and report what it found.

    Goes through ``run_manimgl(dry_run=True)``, so the safety gate decides first
    (a refused file comes back as a crash whose stderr lists the findings, the
    same as for a real render) and the budget is ``rendering.preflight_timeout``.
    Never raises: an unexpected failure of the dry run itself is reported as a
    timeout-like "unknown" result (``ran`` False, ``crashed`` False), so the
    pipeline simply goes on to the real render.
    """
    started = time.monotonic()
    try:
        result = run_manimgl(
            scene_path,
            class_name,
            timeout=paths.preflight_timeout(),
            dry_run=True,
        )
    except Exception as exc:  # noqa: BLE001 - preflight must never sink a section
        logger.warning("[preflight] %s could not run: %s", class_name, exc)
        return PreflightResult(seconds=time.monotonic() - started)
    seconds = time.monotonic() - started
    if result.timed_out:
        logger.warning(
            "[preflight] %s timed out after %.0fs; going on to the real render",
            class_name,
            seconds,
        )
        return PreflightResult(timed_out=True, stderr=result.stderr, seconds=seconds)
    if not result.ok:
        return PreflightResult(
            crashed=True,
            stderr=result.stderr,
            returncode=result.returncode,
            seconds=seconds,
        )
    return PreflightResult(
        ran=True,
        returncode=0,
        overlaps=tuple(result.overlaps or ()),
        kit_findings=kit_findings_from(result.stdout),
        probe_error=result.probe_error,
        seconds=seconds,
    )
