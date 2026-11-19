"""Where the time of one run goes.

A run is a handful of steps (planning, narration, the Director, renders, fixes,
cut and mux, assembly, captions), and sections run in parallel threads, so the
numbers are recorded by a small thread-safe timer instead of being subtracted
from each other afterwards. ``cli.main`` resets the shared ``TIMER`` at the
start, the steps record into it, and ``cli._finish_run`` prints the table and
writes ``logs/timings.json``.

Seconds per step are WALL seconds: the time during which at least one call of
that step was running (overlapping calls are counted once). ``busy`` in the
JSON file is the plain sum of every call. A step's share is its wall seconds
over the run's total, so the shares of steps that overlap in parallel threads
can add up to more than 100%.

LLM calls are timed in one place, ``llm.chat``, and sorted into a step by their
role. Renders are timed in one place too, ``render_command.run_manimgl``
(through the ``timed_render`` decorator), with the size and frame rate each
one used. A render's label ("first render", "retry render", "draft render A",
"fallback render") comes from the ``context`` the caller set.
"""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from collections import Counter
from collections.abc import Callable
from contextlib import contextmanager

FILE_NAME = "timings.json"

# Row order in the table. "other" is whatever no step covers.
STEPS = (
    "planning",
    "narration",
    "director",
    "render",
    "fixes",
    "cut_mux",
    "assembly",
    "captions",
)

# Which step an LLM call belongs to. A role not listed here (or no role) is
# "other".
_ROLE_STEP = {
    "researcher": "planning",
    "planner": "planning",
    "planner_pdf": "planning",
    "critic": "planning",
    "cue_refill": "planning",
    "director": "director",
    "error_fix": "fixes",
    "visual_fix": "fixes",
    "layout_check": "fixes",
}

_local = threading.local()


def step_for_role(role: str | None) -> str:
    return _ROLE_STEP.get(role or "", "other")


class StepTimer:
    """Collects (step, label, start, end) entries from any thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict] = []
        self._origin = time.perf_counter()
        self._started_at = time.time()

    def reset(self) -> None:
        with self._lock:
            self._entries = []
            self._origin = time.perf_counter()
            self._started_at = time.time()

    def record(
        self,
        step: str,
        start: float,
        end: float,
        label: str = "",
        **meta,
    ) -> None:
        """Add one finished call. ``start`` and ``end`` are time.perf_counter() (high resolution on Windows too)."""
        entry = {
            "step": step,
            "label": label,
            "start": max(0.0, start - self._origin),
            "seconds": max(0.0, end - start),
        }
        entry.update({k: v for k, v in meta.items() if v is not None})
        with self._lock:
            self._entries.append(entry)

    @contextmanager
    def section(self, step: str, label: str = "", **meta):
        """Time the ``with`` body as one call of ``step``."""
        start = time.perf_counter()
        try:
            yield
        finally:
            self.record(step, start, time.perf_counter(), label, **meta)

    def entries(self) -> list[dict]:
        with self._lock:
            return [dict(e) for e in self._entries]

    def total(self) -> float:
        """Seconds since the last reset."""
        return time.perf_counter() - self._origin

    def summary(self, total: float | None = None) -> list[dict]:
        """One row per step that ran: calls, wall seconds, busy seconds, share."""
        total = self.total() if total is None else total
        entries = self.entries()
        rows = []
        for step in (*STEPS, "other"):
            mine = [e for e in entries if e["step"] == step]
            if not mine:
                continue
            wall = _union_seconds(
                [(e["start"], e["start"] + e["seconds"]) for e in mine]
            )
            rows.append(
                {
                    "step": step,
                    "calls": len(mine),
                    "seconds": round(wall, 1),
                    "busy_seconds": round(sum(e["seconds"] for e in mine), 1),
                    "share": round(wall / total, 3) if total > 0 else 0.0,
                }
            )
        return rows

    def unmeasured(self, total: float | None = None) -> float:
        """Seconds of the run no step covers (start-up, bookkeeping, waits)."""
        total = self.total() if total is None else total
        spans = [(e["start"], e["start"] + e["seconds"]) for e in self.entries()]
        return max(0.0, total - _union_seconds(spans))

    def render_profiles(self) -> dict[str, int]:
        """Renders by size and rate, e.g. {"540x960@30": 2, "1080x1920@30": 1}."""
        counts = Counter(
            f"{e['resolution']}@{e['fps']}"
            for e in self.entries()
            if e["step"] == "render" and e.get("resolution")
        )
        return dict(counts)

    def to_dict(self, total: float | None = None, **extra) -> dict:
        """The content of timings.json."""
        total = self.total() if total is None else total
        data = {
            "started_at": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(self._started_at)
            ),
            "total_seconds": round(total, 1),
            "unmeasured_seconds": round(self.unmeasured(total), 1),
            "steps": self.summary(total),
            "render_profiles": self.render_profiles(),
        }
        data.update(extra)
        data["calls"] = [
            {**e, "start": round(e["start"], 2), "seconds": round(e["seconds"], 2)}
            for e in sorted(self.entries(), key=lambda e: e["start"])
        ]
        return data

    def table(self, total: float | None = None) -> list[str]:
        """The per-step table as log lines, one line per step."""
        total = self.total() if total is None else total
        profiles = self.render_profiles()
        lines = [f"[manimgen] Time per step (run took {format_seconds(total)}):"]
        for row in self.summary(total):
            noun = "call" if row["calls"] == 1 else "calls"
            line = (
                f"  {row['step']:<10} {row['seconds']:>7.1f}s {row['share']:>5.0%}"
                f"   {row['calls']} {noun}"
            )
            if row["step"] == "render" and profiles:
                line += (
                    " (" + ", ".join(f"{n} at {p}" for p, n in profiles.items()) + ")"
                )
            lines.append(line)
        other = self.unmeasured(total)
        if total > 0:
            lines.append(f"  {'unmeasured':<10} {other:>7.1f}s {other / total:>5.0%}")
        return lines

    def write(
        self,
        directory: str,
        total: float | None = None,
        create: bool = True,
        **extra,
    ) -> str | None:
        """Write timings.json atomically; return its path, or None on failure.

        With ``create=False`` a missing folder is not created (a host app that
        owns logging keeps the logs folder it did not ask for away).
        """
        path = os.path.join(directory, FILE_NAME)
        tmp = path + ".tmp"
        try:
            if create:
                os.makedirs(directory, exist_ok=True)
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(self.to_dict(total, **extra), f, indent=2)
            os.replace(tmp, path)
        except OSError:
            return None
        return path


def _union_seconds(spans: list[tuple[float, float]]) -> float:
    """Total length covered by the spans, overlaps counted once."""
    covered = 0.0
    current_start = current_end = None
    for start, end in sorted(spans):
        if current_end is None or start > current_end:
            if current_end is not None:
                covered += current_end - current_start
            current_start, current_end = start, end
        else:
            current_end = max(current_end, end)
    if current_end is not None:
        covered += current_end - current_start
    return covered


def format_seconds(seconds: float) -> str:
    """'42.0s' under a minute, '6m21s' above."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(int(round(seconds)), 60)
    return f"{minutes}m{rest:02d}s"


# The timer of this process's run.
TIMER = StepTimer()


# ---------------------------------------------------------------------------
# Labels for renders (set by the caller, read by the render hook)
# ---------------------------------------------------------------------------


@contextmanager
def context(label: str):
    """Name what renders started inside this block (per thread)."""
    previous = getattr(_local, "label", "")
    _local.label = label
    try:
        yield
    finally:
        _local.label = previous


def current_label(default: str = "render") -> str:
    return getattr(_local, "label", "") or default


def record_llm_call(role: str | None, start: float, end: float, ok: bool) -> None:
    """Called by llm.chat for every call. Never raises."""
    try:
        TIMER.record(step_for_role(role), start, end, role or "llm", ok=ok)
    except Exception:  # noqa: BLE001 - timing must never fail a call
        pass


def timed_render(func: Callable) -> Callable:
    """Decorator for ``run_manimgl``: time each render with its size and rate."""

    @functools.wraps(func)
    def wrapper(scene_path, class_name, *args, **kwargs):
        start = time.perf_counter()
        result = None
        try:
            result = func(scene_path, class_name, *args, **kwargs)
            return result
        finally:
            try:
                from manimgen import paths

                draft = kwargs.get("draft")
                if draft is None and len(args) >= 2:
                    draft = args[1]
                TIMER.record(
                    "render",
                    start,
                    time.perf_counter(),
                    current_label(),
                    scene=class_name,
                    resolution=paths.render_resolution(draft),
                    fps=paths.native_render_fps(draft),
                    ok=bool(getattr(result, "ok", False)),
                )
            except Exception:  # noqa: BLE001 - timing must never fail a render
                pass

    return wrapper
