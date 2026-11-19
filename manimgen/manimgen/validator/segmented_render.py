"""Render one scene as several manimgl processes at once, then join the pieces.

Why: one manimgl process draws on about one CPU core, and on a machine without a
GPU rendering is most of a Short's wall time. The scene is cut at animation
boundaries into K segments; each is rendered by its own process (``-n start,end``)
and the pieces are joined with ``ffmpeg -c copy``. ``render_command.run_manimgl``
calls :func:`render` after the safety gate and falls back to its normal single
render whenever this returns None, so nothing downstream can tell the
difference: one video, the same frame count, one merged overlap-probe report.

How ``-n start,end`` works in manimgl 1.7.2 (measured, see docs/KNOWN_ISSUES.md):

* The animation number counts every ``play()`` and every ``wait()``, in call order.
* The ``construct`` code runs from the top in every segment. The animations
  before ``start`` run with skipping on (no frames drawn), the segment draws
  animations ``start`` to ``end - 1``, and the scene stops (``EndScene``) when
  animation ``end`` begins. The output file is named ``<Class>_<start>_<end>.mp4``
  (``<Class>_<start>.mp4`` with no end), so parallel segments never collide.
* A skipped animation is NOT the same as a rendered one for updaters and
  ``scene.time``. The probe module patches both inside the child (see
  ``probes/overlap_probe._patch_skipping``); without that patch an updater
  moved by ``dt`` ended up twice as far after a segment boundary.
* There is no cheap way to ask for the animation count. A planning run, ``-n``
  with a start past the end, runs the whole scene in skipping mode (a few
  seconds) and the probe reports ``scene.time`` after every animation.

Pieces are joined losslessly. Every piece starts with a keyframe and shares the
encoder settings, so the stream copy is exact; the join is checked against the
frame count the plan predicts and any mismatch falls back to a single render.
Decoded pixels differ from a single render's only by H.264 encoder noise (about
55 dB PSNR); with an intra-only codec they are identical frame for frame.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

from manimgen import paths, procutil
from manimgen.probes import overlap_probe, overlap_report
from manimgen.validator.render_command import (
    RenderResult,
    build_manimgl_command,
    with_package_path,
    with_utf8_io,
)

logger = logging.getLogger(__name__)

ENV = "MANIMGEN_PARALLEL_SEGMENTS"

# Below this much video one process is as fast: every segment pays the start-up
# (imports, LaTeX, running the skipped animations) again.
MIN_VIDEO_SECONDS = 8.0
MIN_SEGMENT_SECONDS = 3.0
# The longest segment may hold at most this share of the video, or the split
# saves too little to be worth the extra start-ups.
MAX_SEGMENT_SHARE = 0.75
# A planning run past the end of the scene must not be mistaken for a render.
_PLAN_START = 1_000_000
_PLAN_TIMEOUT_SECONDS = 120.0
_JOIN_TIMEOUT_SECONDS = 180.0
_PROBE_TIMEOUT_SECONDS = 30.0
# Segment time allowance: the start-up every process pays plus its share of the
# whole-scene budget, with room for frames that cost more than average.
_SEGMENT_STARTUP_SECONDS = 60.0
_SEGMENT_SLACK = 1.5


# ---------------------------------------------------------------------------
# How many segments (pure, unit-tested)
# ---------------------------------------------------------------------------


def cpu_count() -> int:
    """CPU cores this process may use (affinity-aware where the OS has it)."""
    try:
        return max(1, len(os.sched_getaffinity(0)))  # type: ignore[attr-defined]
    except (AttributeError, OSError):
        return max(1, os.cpu_count() or 1)


def core_budget(cpus: int | None = None) -> int:
    """Cores renders may use together: all but one, which the pipeline keeps."""
    return max(1, (cpus if cpus is not None else cpu_count()) - 1)


def segments_wanted(
    setting: int | None, cpus: int | None = None, concurrent_renders: int = 1
) -> int:
    """Segments one render should aim for, before looking at the scene.

    ``setting`` is ``rendering.parallel_segments``: None means "auto" (the core
    budget split between the renders that run at once, so sections rendered in
    parallel do not oversubscribe the machine); a number is used as given.
    """
    if setting is not None:
        return max(1, int(setting))
    return max(1, core_budget(cpus) // max(1, int(concurrent_renders)))


def segment_count(wanted: int, num_plays: int, total_seconds: float) -> int:
    """Segments to render: ``wanted`` cut down to what the scene can use.

    1 (render normally) for a scene under ``MIN_VIDEO_SECONDS``, and no more
    segments than animations or than one per ``MIN_SEGMENT_SECONDS`` of video.
    """
    if wanted < 2 or total_seconds < MIN_VIDEO_SECONDS or num_plays < 2:
        return 1
    return max(1, min(wanted, num_plays, int(total_seconds // MIN_SEGMENT_SECONDS)))


def split_plays(play_times: list[float], k: int) -> list[tuple[int, int | None]]:
    """Cut animations 0..n-1 into ``k`` runs of about equal video time.

    ``play_times[i]`` is the scene time after animation ``i``. Returns
    ``(start, end)`` pairs for ``-n``; the last end is None (run to the end).
    Fewer than ``k`` pairs come back when the scene cannot be cut that often.
    """
    n = len(play_times)
    k = min(k, n)
    if k < 2:
        return [(0, None)]
    total = play_times[-1]
    cuts: list[int] = []
    for j in range(1, k):
        target = total * j / k
        low = (cuts[-1] + 1) if cuts else 1
        high = n - (k - j)  # leave one animation for every later segment
        best = min(
            range(low, high + 1),
            key=lambda c: abs(play_times[c - 1] - target),
            default=None,
        )
        if best is None:
            break
        cuts.append(best)
    starts = [0, *cuts]
    ends: list[int | None] = [*cuts, None]
    return list(zip(starts, ends))


def segment_shares(
    play_times: list[float], ranges: list[tuple[int, int | None]]
) -> list[float]:
    """Share of the video each range holds (they sum to 1)."""
    total = play_times[-1] if play_times else 0.0
    if total <= 0:
        return [1.0 / max(1, len(ranges))] * len(ranges)
    shares = []
    for start, end in ranges:
        t0 = play_times[start - 1] if start > 0 else 0.0
        t1 = play_times[(end if end is not None else len(play_times)) - 1]
        shares.append((t1 - t0) / total)
    return shares


def segment_timeout(whole: float, share: float) -> float:
    """Seconds one segment may run: its share of the budget plus the start-up."""
    extra = max(0.0, whole - _SEGMENT_STARTUP_SECONDS) * share * _SEGMENT_SLACK
    return min(whole, _SEGMENT_STARTUP_SECONDS + extra)


# ---------------------------------------------------------------------------
# Settings and the shared core budget
# ---------------------------------------------------------------------------

_concurrent_renders = 1


def set_concurrent_renders(n: int) -> None:
    """Tell this module how many renders the pipeline runs at once.

    ``cli.py`` sets it to the number of sections it renders in parallel, so the
    core budget is split between them instead of each render taking all of it.
    """
    global _concurrent_renders
    _concurrent_renders = max(1, int(n))


def requested_segments() -> int | None:
    """None for "auto", else the segment count (env, then config)."""
    return paths.render_parallel_segments()


class CoreBudget:
    """Counts the cores renders hold, so parallel renders do not oversubscribe.

    A single render always runs (it holds one core, even when none is free); a
    segmented render takes only cores that are free and is not used when fewer
    than two are.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_use = 0

    @contextmanager
    def hold(self, want: int, total: int, minimum: int = 0):
        with self._lock:
            granted = max(minimum, min(want, total - self._in_use))
            self._in_use += granted
        try:
            yield granted
        finally:
            with self._lock:
                self._in_use -= granted

    @property
    def in_use(self) -> int:
        return self._in_use


BUDGET = CoreBudget()


# ---------------------------------------------------------------------------
# Merging the probe reports (pure, unit-tested)
# ---------------------------------------------------------------------------


def merge_reports(reports: list[dict], scene: str = "") -> dict:
    """Combine per-segment probe reports into the report one render would write.

    Each segment reports only the animations it rendered (the probe skips the
    ones it fast-forwards), in scene time, so the findings concatenate in order.
    They are replayed through the probe's own de-duplication and caps, so the
    merged list is the one a single render would have produced.
    """
    recorder = overlap_probe.OverlapRecorder()
    dropped = 0
    for rep in reports:
        dropped += int(rep.get("dropped") or 0)
        for item in rep.get("findings") or []:
            try:
                recorder._add(
                    overlap_probe.Finding(
                        float(item["time"]),
                        str(item["a"]),
                        str(item["b"]),
                        float(item["fraction"]),
                        str(item.get("a_kind", "Text")),
                        str(item.get("b_kind", "Text")),
                        str(item.get("kind") or "overlap"),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
    merged = recorder.report(scene or _first(reports, "scene", ""), None)
    merged["dropped"] = dropped + recorder.dropped
    merged["checks"] = sum(int(r.get("checks") or 0) for r in reports)
    merged["nudges"] = sum(int(r.get("nudges") or 0) for r in reports)
    below = max(reports, key=lambda r: r.get("max_fraction_below_threshold") or 0.0)
    merged["max_fraction_below_threshold"] = below.get(
        "max_fraction_below_threshold", 0.0
    )
    merged["max_below_pair"] = below.get("max_below_pair", [])
    merged["max_outline_share_below_threshold"] = max(
        (r.get("max_outline_share_below_threshold") or 0.0 for r in reports),
        default=0.0,
    )
    merged["min_size_ratio"] = min(
        (r.get("min_size_ratio", 9.9) for r in reports), default=9.9
    )
    merged["morphs"] = [m for r in reports for m in (r.get("morphs") or [])][:50]
    merged["probe_error"] = next(
        (r["probe_error"] for r in reports if r.get("probe_error")), None
    )
    merged["num_plays"] = max(
        (int(r.get("num_plays") or 0) for r in reports), default=0
    )
    merged["play_times"] = max(
        (r.get("play_times") or [] for r in reports), key=len, default=[]
    )
    merged["segments"] = len(reports)
    return merged


def _first(reports: list[dict], key: str, default):
    for r in reports:
        if r.get(key):
            return r[key]
    return default


def _read_json(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _estimate(code: str) -> tuple[float, int]:
    """(lower-bound seconds, unknown-duration calls) of the scene's play/wait calls."""
    from manimgen.validator.timing_verifier import (
        _construct_body_source,
        _parse_cue_block,
        _time_for_statements,
    )

    body = _construct_body_source(code)
    tree = _parse_cue_block(body) if body else None
    if tree is None:
        return 0.0, 1
    return _time_for_statements(tree.body)


def _child_env(report_path: str, passive: bool) -> dict[str, str]:
    from manimgen.validator.env import get_render_env

    env = with_package_path(with_utf8_io(get_render_env()))
    env = overlap_report.with_probe_env(
        env, report_path, paths.short_safe_margins() if paths.is_short() else None
    )
    if passive:
        env[overlap_probe.PASSIVE_ENV] = "1"
    return env


def segment_command(
    scene_path: str,
    class_name: str,
    draft: bool | None,
    start: int,
    end: int | None,
    out_dir: str,
) -> list[str]:
    """The normal render command, limited to animations ``start`` to ``end``."""
    number = str(start) if end is None else f"{start},{end}"
    return [
        *build_manimgl_command(scene_path, class_name, draft),
        "-n",
        number,
        "--video_dir",
        out_dir,
    ]


def segment_file_name(class_name: str, start: int, end: int | None) -> str:
    """The file manimgl names a segment's video (it appends the numbers)."""
    return (
        f"{class_name}_{start}.mp4"
        if end is None
        else (f"{class_name}_{start}_{end}.mp4")
    )


def _plan(
    scene_path: str,
    class_name: str,
    draft: bool | None,
    tmp: str,
    timeout: float,
    passive: bool,
) -> list[float] | None:
    """Run the scene with every animation skipped; return the time after each."""
    report = str(Path(tmp) / "plan.json")
    code, _out, err, timed_out = procutil.run_tree(
        segment_command(scene_path, class_name, draft, _PLAN_START, None, tmp),
        timeout=timeout,
        env=_child_env(report, passive),
    )
    data = _read_json(report) or {}
    times = data.get("play_times")
    if timed_out or code != 0 or not times:
        logger.info(
            "[render] segment plan for %s failed (%s); rendering in one piece",
            class_name,
            "timeout" if timed_out else (err or "no animations reported")[-200:],
        )
        return None
    return [float(t) for t in times]


def _expected_frames(play_times: list[float], fps: float) -> int:
    return round(play_times[-1] * fps)


def _probe_video(path: str) -> tuple[int, float] | None:
    """(frames, fps) of a video from ffprobe, or None when it cannot be read."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    code, out, _err, timed_out = procutil.run_tree(
        [
            ffprobe,
            "-v",
            "error",
            "-count_packets",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_packets,r_frame_rate",
            "-of",
            "json",
            path,
        ],
        timeout=_PROBE_TIMEOUT_SECONDS,
    )
    if timed_out or code != 0:
        return None
    try:
        stream = json.loads(out)["streams"][0]
        num, _, den = str(stream["r_frame_rate"]).partition("/")
        return int(stream["nb_read_packets"]), float(num) / float(den or 1)
    except (KeyError, IndexError, ValueError, ZeroDivisionError):
        return None


def _join(pieces: list[str], target: str) -> str | None:
    """Stream-copy ``pieces`` into ``target``; return an error text or None."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return "ffmpeg not found"
    folder = os.path.dirname(os.path.abspath(pieces[0]))
    listing = Path(folder) / "join.txt"

    def entry(piece: str) -> str:
        path = Path(piece).resolve().as_posix().replace("'", "'\\''")
        return f"file '{path}'\n"

    listing.write_text("".join(entry(p) for p in pieces), encoding="utf-8")
    code, _out, err, timed_out = procutil.run_tree(
        [
            ffmpeg,
            "-v",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(listing),
            "-c",
            "copy",
            "-an",
            target,
        ],
        timeout=_JOIN_TIMEOUT_SECONDS,
    )
    if timed_out:
        return "ffmpeg join timed out"
    return None if code == 0 else (err or f"ffmpeg exited {code}")[-300:]


def render(
    scene_path: str,
    class_name: str,
    timeout: float,
    draft: bool | None = None,
) -> RenderResult | None:
    """Render ``class_name`` in parallel segments, or return None.

    None means "use the normal single render": parallel rendering is off, the
    scene is too short or cannot be balanced, a core budget leaves no room, or
    any step failed. A timeout is returned as a failed result instead, because
    a single render of the same scene would not fit the budget either.
    """
    setting = requested_segments()
    if setting == 1:
        return None
    wanted = segments_wanted(setting, concurrent_renders=_concurrent_renders)
    if wanted < 2:
        return None

    try:
        with open(scene_path, encoding="utf-8") as f:
            code = f.read()
    except OSError:
        return None
    seconds, unknown = _estimate(code)
    if unknown == 0 and seconds < MIN_VIDEO_SECONDS:
        return None  # known to be short: not worth a planning run

    total_cores = core_budget() if setting is None else max(wanted, core_budget())
    with BUDGET.hold(wanted, total_cores) as granted:
        if granted < 2:
            return None
        return _render_held(scene_path, class_name, timeout, draft, granted)


def _render_held(
    scene_path: str, class_name: str, timeout: float, draft: bool | None, granted: int
) -> RenderResult | None:
    from manimgen.validator.runner import _find_rendered_video, _render_floor

    started = time.monotonic()
    floor = _render_floor()
    passive = not overlap_report.probe_enabled()
    tmp = tempfile.mkdtemp(prefix="manimgen_segments_")
    try:
        times = _plan(
            scene_path,
            class_name,
            draft,
            tmp,
            min(timeout, _PLAN_TIMEOUT_SECONDS),
            passive,
        )
        if times is None:
            return None
        count = segment_count(granted, len(times), times[-1])
        if count < 2:
            logger.info(
                "[render] %s: %d animations, %.1f s; rendering in one piece",
                class_name,
                len(times),
                times[-1],
            )
            return None
        ranges = split_plays(times, count)
        shares = segment_shares(times, ranges)
        if len(ranges) < 2 or max(shares) > MAX_SEGMENT_SHARE:
            logger.info(
                "[render] %s: cannot be split evenly (largest piece %.0f%%); "
                "rendering in one piece",
                class_name,
                100 * max(shares),
            )
            return None
        plan_seconds = time.monotonic() - started
        logger.info(
            "[render] %s: %d segments %s, plan took %.1f s",
            class_name,
            len(ranges),
            ranges,
            plan_seconds,
        )

        cancel = threading.Event()
        outcomes: list[dict] = [{} for _ in ranges]

        def run_one(i: int) -> None:
            start, end = ranges[i]
            out_dir = str(Path(tmp) / f"seg{i}")
            report = str(Path(tmp) / f"report{i}.json")
            code, out, err, timed_out = procutil.run_tree(
                segment_command(scene_path, class_name, draft, start, end, out_dir),
                timeout=segment_timeout(timeout, shares[i]),
                env=_child_env(report, passive),
                cancel=cancel,
            )
            piece = Path(out_dir) / segment_file_name(class_name, start, end)
            ok = (
                not timed_out
                and code == 0
                and piece.is_file()
                and piece.stat().st_size > 0
            )
            outcomes[i] = {
                "ok": ok,
                "timed_out": timed_out,
                "code": code,
                "out": out,
                "err": err,
                "piece": str(piece),
                "report": report,
                "cancelled": cancel.is_set() and not ok and not timed_out,
            }
            if not ok:
                cancel.set()  # stop the siblings: the whole render falls back

        with ThreadPoolExecutor(max_workers=len(ranges)) as pool:
            list(pool.map(run_one, range(len(ranges))))

        failed = [o for o in outcomes if not o.get("ok")]
        if failed:
            culprit = next((o for o in failed if not o["cancelled"]), failed[0])
            if culprit["timed_out"]:
                note = (
                    f"TimeoutExpired: a render segment of {class_name} exceeded its "
                    f"time allowance of the {timeout:g} second budget "
                    "(process trees killed)."
                )
                return RenderResult(False, None, culprit["out"], note, None, True)
            logger.warning(
                "[render] %s: a segment failed (exit %s); rendering in one piece: %s",
                class_name,
                culprit["code"],
                (culprit["err"] or "")[-300:],
            )
            return None

        pieces = [o["piece"] for o in outcomes]
        joined = str(Path(tmp) / f"{class_name}.mp4")
        problem = _join(pieces, joined)
        if problem is not None:
            logger.warning(
                "[render] %s: join failed (%s); rendering in one piece",
                class_name,
                problem,
            )
            return None
        measured = _probe_video(joined)
        if measured is not None:
            frames, fps = measured
            if frames != _expected_frames(times, fps):
                logger.warning(
                    "[render] %s: joined video has %d frames, the plan says %d; "
                    "rendering in one piece",
                    class_name,
                    frames,
                    _expected_frames(times, fps),
                )
                return None

        out_dir = Path("videos")
        out_dir.mkdir(parents=True, exist_ok=True)
        final = out_dir / f"{class_name}.mp4"
        staged = out_dir / f".{class_name}.segments.mp4"
        shutil.move(joined, staged)
        os.replace(staged, final)
        video = _find_rendered_video(class_name, newer_than=floor)
        if video is None:
            logger.warning(
                "[render] %s: joined video not found; single render", class_name
            )
            return None

        reports = [r for r in (_read_json(o["report"]) for o in outcomes) if r]
        merged = merge_reports(reports, class_name) if reports else None
        report_path = None
        if merged is not None and not passive:
            report_path = str(Path(tmp) / "merged.json")
            Path(report_path).write_text(
                json.dumps(merged, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        overlap_report.save_sidecar(video, report_path)
        report = (
            overlap_report.read_report(report_path)
            if report_path
            else overlap_report.OverlapReport()
        )
        logger.info(
            "[render] %s: %d segments joined in %.1f s",
            class_name,
            len(ranges),
            time.monotonic() - started,
        )
        stdout = "\n".join(
            f"[segment {i}] {o['out']}".rstrip() for i, o in enumerate(outcomes)
        )
        return RenderResult(
            True,
            video,
            stdout,
            "",
            0,
            False,
            overlaps=report.overlaps,
            probe_error=report.probe_error,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
