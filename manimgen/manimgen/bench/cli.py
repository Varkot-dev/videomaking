"""``python -m manimgen.bench {run,score,sheet}``."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from manimgen.bench import RUNS_DIR
from manimgen.bench import score as score_mod


def _parse_env(items: list[str]) -> dict[str, str]:
    env = {}
    for item in items:
        key, sep, val = item.partition("=")
        if not sep or not key:
            raise SystemExit(f"--env expects KEY=VAL, got {item!r}")
        env[key] = val
    return env


def _cmd_run(args) -> int:
    from manimgen.bench import runner
    from manimgen.bench.topics import load_topics

    topics = load_topics(only=args.only.split(",") if args.only else None)
    dirs = runner.run_all(
        topics,
        args.variant,
        _parse_env(args.env),
        jobs=args.jobs,
        draft=not args.final,
    )
    print(f"ran {len(dirs)} topics under {RUNS_DIR / args.variant}")
    return _score(argparse.Namespace(variants=[args.variant], dir=None))


def _score(args) -> int:
    exp = Path(args.dir) if args.dir else None
    results = [score_mod.score_variant(v, RUNS_DIR, exp) for v in args.variants]
    for res in results:
        if not res["topics"]:
            print(f"no runs found for variant {res['variant']!r}")
            continue
        md = score_mod.render_markdown(res)
        print(md)
        out = (
            RUNS_DIR / res["variant"]
            if exp is None
            else exp / "_scorecards" / res["variant"]
        )
        out.mkdir(parents=True, exist_ok=True)
        (out / "scorecard.md").write_text(md, encoding="utf-8")
        (out / "scorecard.json").write_text(json.dumps(res, indent=2), encoding="utf-8")
    if len(results) > 1:
        print(score_mod.render_comparison(results))
    return 0


def _cmd_sheet(args) -> int:
    from manimgen.bench import sheet

    exp = Path(args.dir) if args.dir else None
    for variant in args.variants:
        vids = []
        for slug, d in score_mod.variant_dirs(variant, RUNS_DIR, exp):
            v = score_mod.find_video(d)
            if v is not None:
                vids.append((slug, v))
        if not vids:
            print(f"no videos for variant {variant!r}")
            continue
        base = RUNS_DIR / variant if exp is None else exp / "_scorecards" / variant
        out = sheet.make_sheet([v for _, v in vids], base / "frames.png")
        print(f"{out}  rows: {', '.join(s for s, _ in vids)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m manimgen.bench")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="plan (cached) and render the topics, then score")
    r.add_argument("--only", help="comma-separated slugs")
    r.add_argument("--variant", default="default")
    r.add_argument("--env", action="append", default=[], metavar="KEY=VAL")
    r.add_argument("--jobs", type=int, default=1)
    r.add_argument(
        "--final",
        action="store_true",
        help="render at full quality (default: the test profile, small and 30 fps)",
    )
    r.set_defaults(func=_cmd_run)

    s = sub.add_parser("score", help="scorecard for one or more variants")
    s.add_argument("variants", nargs="+")
    s.add_argument("--dir", help="read an <dir>/<slug>/<variant>/ experiment tree")
    s.set_defaults(func=_score)

    f = sub.add_parser("sheet", help="frame sheet PNG per variant")
    f.add_argument("variants", nargs="+")
    f.add_argument("--dir", help="read an <dir>/<slug>/<variant>/ experiment tree")
    f.set_defaults(func=_cmd_sheet)

    args = p.parse_args(argv)
    return args.func(args)
