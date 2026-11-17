"""Host side of the render-time text overlap probe (#97).

``with_probe_env`` prepares the manimgl child's environment so
``overlap_probe`` runs inside it; ``read_report`` parses what it wrote. The
findings become ``OVERLAP:`` issue lines that ``validate_render`` and
``retry_scene`` treat as hard visual defects.

A copy of the last report is kept next to the rendered video as
``<video>.overlaps.json`` so it can be inspected after a run and re-read by the
first-pass validator.

Set ``MANIMGEN_OVERLAP_PROBE=0`` to switch the probe off.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

REPORT_ENV = "MANIMGEN_OVERLAP_REPORT"  # must match overlap_probe.REPORT_ENV
DISABLE_ENV = "MANIMGEN_OVERLAP_PROBE"
SAFE_MARGINS_ENV = "MANIMGEN_SAFE_MARGINS"  # must match overlap_probe.SAFE_MARGINS_ENV
SIDECAR_SUFFIX = ".overlaps.json"
ISSUE_PREFIX = "OVERLAP:"


@dataclass(frozen=True)
class Overlap:
    """Two texts drawn on top of each other at one stable moment."""

    time: float
    a: str
    b: str
    fraction: float
    # "overlap" (a drawn over b), "offscreen" (a cut off by the frame edge) or
    # "unsafe" (a under the Shorts buttons or caption). b is "" for the last two.
    kind: str = "overlap"


@dataclass(frozen=True)
class OverlapReport:
    overlaps: tuple[Overlap, ...] = ()
    probe_error: str | None = None
    present: bool = False  # False: the probe did not run or wrote nothing


def probe_enabled() -> bool:
    return os.environ.get(DISABLE_ENV, "1").strip().lower() not in ("0", "false", "off")


def bootstrap_dir() -> str:
    """The directory holding the child's ``sitecustomize.py``."""
    return str(Path(__file__).resolve().parent / "bootstrap")


def with_probe_env(
    env: dict[str, str],
    report_path: str,
    safe_margins: tuple[float, float, float, float] | None = None,
) -> dict[str, str]:
    """Return ``env`` with the probe bootstrap first on PYTHONPATH.

    Uses ``os.pathsep`` (``;`` on Windows, ``:`` elsewhere) and keeps any
    PYTHONPATH the caller already had after the bootstrap directory.
    ``safe_margins`` (left, bottom, right, top as fractions of the frame) turns
    on the safe-area check used for vertical shorts.
    """
    env = dict(env)
    env.pop(SAFE_MARGINS_ENV, None)
    if safe_margins is not None:
        env[SAFE_MARGINS_ENV] = ",".join(f"{m:.5f}" for m in safe_margins)
    existing = env.get("PYTHONPATH", "")
    parts = [bootstrap_dir()] + [p for p in existing.split(os.pathsep) if p]
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env[REPORT_ENV] = str(report_path)
    return env


def _parse(data: object) -> OverlapReport:
    if not isinstance(data, dict):
        return OverlapReport(probe_error="report is not a JSON object", present=True)
    overlaps = []
    for item in data.get("findings") or []:
        try:
            overlaps.append(
                Overlap(
                    time=float(item["time"]),
                    a=str(item["a"]),
                    b=str(item["b"]),
                    fraction=float(item["fraction"]),
                    kind=str(item.get("kind") or "overlap"),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    error = data.get("probe_error")
    return OverlapReport(
        overlaps=tuple(overlaps),
        probe_error=str(error) if error else None,
        present=True,
    )


def read_report(path: str | os.PathLike) -> OverlapReport:
    """Parse a probe report; a missing or unreadable file is an empty report."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return OverlapReport()
    except (OSError, ValueError) as exc:
        return OverlapReport(probe_error=f"unreadable report: {exc}", present=True)
    return _parse(data)


def sidecar_path(video_path: str) -> str:
    return video_path + SIDECAR_SUFFIX


def save_sidecar(video_path: str, report_path: str | None) -> None:
    """Copy the child's report next to the video (remove a stale copy if none)."""
    target = sidecar_path(video_path)
    try:
        if report_path is None:
            raise FileNotFoundError(target)
        with open(report_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        try:
            os.remove(target)
        except OSError:
            pass
        return
    try:
        with open(target, "w", encoding="utf-8") as f:
            f.write(text)
    except OSError as exc:
        logger.debug("[overlap] could not save %s: %s", target, exc)


def load_for_video(video_path: str) -> OverlapReport:
    return read_report(sidecar_path(video_path))


def format_issue(overlap: Overlap) -> str:
    """One hard-defect line for the visual-repair prompt."""
    if overlap.kind == "offscreen":
        return (
            f"{ISSUE_PREFIX} text {overlap.a!r} is cut off by the edge of the "
            f"frame at t={overlap.time:.1f}s ({overlap.fraction:.0%} of it is "
            "off screen). Move it inward, split it over two lines, or guard its "
            "width with set_width so the whole text is visible."
        )
    if overlap.kind == "unsafe":
        return (
            f"{ISSUE_PREFIX} text {overlap.a!r} sits outside the safe area of "
            f"the vertical frame at t={overlap.time:.1f}s ({overlap.fraction:.0%} "
            "of it is under the app's buttons or caption). Keep all text inside "
            "x -2.0 to 1.4 and y -2.0 to 2.8: move it up or left, split it over "
            "two lines, or guard its width with set_width(3.3)."
        )
    return (
        f"{ISSUE_PREFIX} text {overlap.a!r} is drawn on top of {overlap.b!r} "
        f"at t={overlap.time:.1f}s ({overlap.fraction:.0%} of the smaller box). "
        "Reposition one of them (next_to / shift / arrange in a VGroup), "
        "fade or remove the earlier text before showing the new one, or "
        "shorten/scale it so they no longer overlap."
    )


def overlap_issues(overlaps) -> list[str]:
    return [format_issue(o) for o in overlaps]
