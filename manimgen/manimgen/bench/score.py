"""Turn run folders into numbers.

A run folder is what ``manimgen --resume`` leaves behind: ``run.log``, ``wall``
(seconds), ``logs/llm_usage.jsonl`` and ``videos/*.mp4`` with ``*.overlaps.json``
next to each video.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from manimgen.bench import RUNS_DIR

OUTCOMES = ("ok", "accepted_with_defects", "fallback")
FINDING_KINDS = ("overlap", "offscreen", "unsafe")

_SUMMARY = re.compile(r"Run summary for .*?: (\d+) sections? \(([^)]*)\)")
_COUNT = re.compile(r"(\d+)\s+([a-z_]+)")
_FIX_REQUEST = re.compile(r"\[retry\] Requesting .*fix")


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_log(text: str) -> dict:
    """Outcome, section count, retries and render estimate from run.log."""
    sections = 0
    counts: dict[str, int] = {}
    for m in _SUMMARY.finditer(text):  # keep the last summary line
        sections = int(m.group(1))
        counts = {s: int(n) for n, s in _COUNT.findall(m.group(2))}
    if not counts:
        outcome = "no_summary"
    elif counts.get("fallback"):
        outcome = "fallback"
    elif counts.get("accepted_with_defects"):
        outcome = "accepted_with_defects"
    elif counts.get("ok") == sections:
        outcome = "ok"
    else:
        outcome = "other:" + ",".join(sorted(counts))
    retry_attempts = len(re.findall(r"\[retry\] Attempt \d", text))
    fixes = len(_FIX_REQUEST.findall(text))
    # One render per section plus one per fix the retry loop asked for.
    renders = (sections + fixes) if counts else 0
    return {
        "outcome": outcome,
        "sections": sections,
        "retry_lines": retry_attempts,
        "fix_requests": fixes,
        "renders": renders,
        "first_try": outcome == "ok" and retry_attempts == 0,
    }


def parse_usage(path: Path) -> dict:
    out = {"director_s": 0.0, "director_tokens": 0, "other_llm_s": 0.0, "llm_calls": 0}
    for line in _read_text(path).splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        out["llm_calls"] += 1
        dur = float(row.get("duration_s") or 0)
        if row.get("role") == "director":
            out["director_s"] += dur
            out["director_tokens"] += int(row.get("output_tokens") or 0)
        else:
            out["other_llm_s"] += dur
    return out


def parse_probes(videos_dir: Path) -> dict:
    out = {k: 0 for k in FINDING_KINDS}
    out["nudges"] = 0
    for f in sorted(videos_dir.glob("*.overlaps.json")):
        try:
            data = json.loads(_read_text(f))
        except ValueError:
            continue
        for fd in data.get("findings") or []:
            kind = fd.get("kind")
            if kind in out:
                out[kind] += 1
        out["nudges"] += int(data.get("nudges") or 0)
    return out


def parse_timings(path: Path) -> dict:
    """Per-step seconds from logs/timings.json (all None for an older run)."""
    out = {"render_s": None, "fixes_s": None, "other_s": None, "profile": None}
    try:
        data = json.loads(_read_text(path))
    except ValueError:
        return out
    if not isinstance(data, dict) or "steps" not in data:
        return out
    wall = {
        s.get("step"): float(s.get("seconds") or 0)
        for s in data["steps"]
        if isinstance(s, dict)
    }
    total = float(data.get("total_seconds") or 0)
    covered = sum(wall.get(k, 0.0) for k in ("director", "render", "fixes"))
    out["render_s"] = wall.get("render", 0.0)
    out["fixes_s"] = wall.get("fixes", 0.0)
    out["other_s"] = max(0.0, total - covered)
    if data.get("profile"):
        out["profile"] = str(data["profile"])
        if data.get("resolution") and data.get("fps"):
            out["profile"] += f" {data['resolution']}@{data['fps']}"
    return out


def find_video(run_dir: Path) -> Path | None:
    """The final video: the newest top-level mp4 that is not a Section scene."""
    vids = [p for p in (run_dir / "videos").glob("*.mp4") if p.is_file()]
    final = [p for p in vids if not re.match(r"Section\d+Scene", p.name)]
    pool = final or vids
    return max(pool, key=lambda p: p.stat().st_mtime) if pool else None


def score_run(run_dir: Path) -> dict:
    row = parse_log(_read_text(run_dir / "run.log"))
    try:
        row["wall_s"] = float(_read_text(run_dir / "wall").strip())
    except ValueError:
        row["wall_s"] = None
    row.update(parse_usage(run_dir / "logs" / "llm_usage.jsonl"))
    row.update(parse_timings(run_dir / "logs" / "timings.json"))
    row.update(parse_probes(run_dir / "videos"))
    row["has_video"] = find_video(run_dir) is not None
    return row


def variant_dirs(variant: str, runs_dir: Path = RUNS_DIR, exp_dir: Path | None = None):
    """Yield (slug, run_dir) for one variant.

    Bench layout is ``<runs>/<variant>/<slug>/``; the older experiment layout
    (``exp_dir``) is ``<exp>/<slug>/<variant>/``.
    """
    if exp_dir is not None:
        for slug_dir in sorted(p for p in exp_dir.iterdir() if p.is_dir()):
            d = slug_dir / variant
            if (d / "run.log").exists():
                yield slug_dir.name, d
        return
    root = runs_dir / variant
    if root.is_dir():
        for d in sorted(p for p in root.iterdir() if p.is_dir()):
            if (d / "run.log").exists():
                yield d.name, d


def score_variant(variant: str, runs_dir: Path = RUNS_DIR, exp_dir: Path | None = None):
    topics = {
        slug: score_run(d) for slug, d in variant_dirs(variant, runs_dir, exp_dir)
    }
    return {"variant": variant, "topics": topics, "total": totals(topics)}


def totals(topics: dict[str, dict]) -> dict:
    rows = list(topics.values())
    n = len(rows)
    t: dict = {"topics": n}
    for key in (
        "wall_s",
        "director_s",
        "director_tokens",
        "other_llm_s",
        "render_s",
        "fixes_s",
        "other_s",
        "renders",
        *FINDING_KINDS,
        "nudges",
    ):
        t[key] = round(sum(r.get(key) or 0 for r in rows), 1)
    for o in OUTCOMES:
        t[o] = sum(1 for r in rows if r["outcome"] == o)
    t["first_try"] = sum(1 for r in rows if r["first_try"])
    profiles = sorted({r["profile"] for r in rows if r.get("profile")})
    t["profile"] = ", ".join(profiles) if profiles else None
    return t


_COLS = (
    ("topic", None),
    ("wall s", "wall_s"),
    ("director s", "director_s"),
    ("dir tokens", "director_tokens"),
    ("other llm s", "other_llm_s"),
    ("render s", "render_s"),
    ("fixes s", "fixes_s"),
    ("other s", "other_s"),
    ("profile", "profile"),
    ("outcome", "outcome"),
    ("1st try", "first_try"),
    ("renders", "renders"),
    ("overlap", "overlap"),
    ("offscreen", "offscreen"),
    ("unsafe", "unsafe"),
    ("nudges", "nudges"),
)


def _fmt(v) -> str:
    if v is None:
        return "-"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:.0f}" if v == int(v) or abs(v) >= 100 else f"{v:.1f}"
    return str(v)


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = [
        "| " + " | ".join(header) + " |",
        "|" + "|".join("---" for _ in header) + "|",
    ]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def render_markdown(result: dict) -> str:
    rows = []
    for slug, r in result["topics"].items():
        rows.append([slug] + [_fmt(r.get(k)) for _, k in _COLS[1:]])
    t = result["total"]
    rows.append(
        [
            f"**total ({t['topics']})**",
            _fmt(t["wall_s"]),
            _fmt(t["director_s"]),
            _fmt(t["director_tokens"]),
            _fmt(t["other_llm_s"]),
            _fmt(t["render_s"]),
            _fmt(t["fixes_s"]),
            _fmt(t["other_s"]),
            _fmt(t["profile"]),
            f"{t['ok']} ok / {t['accepted_with_defects']} defects / {t['fallback']} fallback",
            f"{t['first_try']}/{t['topics']}",
            _fmt(t["renders"]),
            _fmt(t["overlap"]),
            _fmt(t["offscreen"]),
            _fmt(t["unsafe"]),
            _fmt(t["nudges"]),
        ]
    )
    return f"## {result['variant']}\n\n" + _table([c for c, _ in _COLS], rows) + "\n"


def render_comparison(results: list[dict]) -> str:
    header = [
        "variant",
        "topics",
        "wall s",
        "director s",
        "dir tokens",
        "render s",
        "fixes s",
        "other s",
        "profile",
        "ok",
        "defects",
        "fallback",
        "1st try",
        "renders",
        "overlap",
        "offscreen",
        "unsafe",
        "nudges",
    ]
    rows = []
    for res in results:
        t = res["total"]
        rows.append(
            [
                res["variant"],
                str(t["topics"]),
                _fmt(t["wall_s"]),
                _fmt(t["director_s"]),
                _fmt(t["director_tokens"]),
                _fmt(t["render_s"]),
                _fmt(t["fixes_s"]),
                _fmt(t["other_s"]),
                _fmt(t["profile"]),
                str(t["ok"]),
                str(t["accepted_with_defects"]),
                str(t["fallback"]),
                str(t["first_try"]),
                _fmt(t["renders"]),
                _fmt(t["overlap"]),
                _fmt(t["offscreen"]),
                _fmt(t["unsafe"]),
                _fmt(t["nudges"]),
            ]
        )
    return "## Comparison\n\n" + _table(header, rows) + "\n"
