"""Turn accumulated production evidence logs into the resolution-rate metric.

`eval/run_corpus.py` measures Codeguard against a curated corpus. This script
measures the SAME metric against whatever real runs actually produced, by
reading the JSONL written by `manimgen/validator/evidence_log.py`.

The two numbers are directly comparable by construction: both ask "after the
static repair path ran, did the scene pass Codeguard's own validation?" If a
real run disagrees with the corpus number, the corpus is unrepresentative and
should be extended — that is the point of wiring this up.

What it reports
---------------
- production resolution rate: share of `precheck` events with validation_clean
- rule firing frequency, so corpus rule coverage can be compared to reality
- shadow-check volume (unknown symbols / invalid kwargs) — the leading
  indicator for whether enforcement is worth enabling
- A/V mismatch counts from the muxer

Usage:
    python3 eval/aggregate_logs.py                       # default output/logs/
    python3 eval/aggregate_logs.py --log PATH --json OUT
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manimgen.validator.evidence_log import evidence_path, read_events  # noqa: E402


def aggregate(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce raw evidence events to the comparable resolution-rate summary."""
    prechecks = [e for e in events if e.get("event") == "precheck"]
    clean = sum(1 for e in prechecks if e.get("validation_clean"))
    changed = sum(1 for e in prechecks if e.get("code_changed"))

    rules: Counter[str] = Counter()
    for e in prechecks:
        for rule in e.get("rules_fired") or []:
            rules[re.sub(r"\s*\(\d+\)$", "", str(rule))] += 1

    first_errors: Counter[str] = Counter()
    for e in prechecks:
        err = e.get("first_error")
        if err:
            # Bucket by error head so line numbers don't fragment the counts.
            first_errors[str(err).split("(")[0].strip()[:80]] += 1

    unknown = [e for e in events if e.get("event") == "shadow_unknown_symbols"]
    invalid = [e for e in events if e.get("event") == "shadow_invalid_kwargs"]
    mismatches = [e for e in events if e.get("event") == "av_mismatch"]

    sym: Counter[str] = Counter()
    for e in unknown:
        for s in e.get("symbols") or []:
            sym[str(s)] += 1

    kw: Counter[str] = Counter()
    for e in invalid:
        for item in e.get("kwargs") or []:
            if isinstance(item, dict):
                kw[f"{item.get('class')}(...{item.get('kwarg')}=)"] += 1

    return {
        "resolution": {
            "precheck_events": len(prechecks),
            "validation_clean": clean,
            "rate_pct": round(100.0 * clean / len(prechecks), 1) if prechecks else 0.0,
            "code_changed": changed,
            "change_rate_pct": (
                round(100.0 * changed / len(prechecks), 1) if prechecks else 0.0
            ),
        },
        "rules_fired": dict(rules.most_common()),
        "blocking_errors": dict(first_errors.most_common()),
        "shadow": {
            "unknown_symbol_events": len(unknown),
            "top_unknown_symbols": dict(sym.most_common(20)),
            "invalid_kwarg_events": len(invalid),
            "top_invalid_kwargs": dict(kw.most_common(20)),
        },
        "av_mismatch": {
            "count": len(mismatches),
            "large": sum(1 for m in mismatches if m.get("large")),
        },
        "total_events": len(events),
    }


def _render(summary: dict[str, Any], log_path: str) -> str:
    r = summary["resolution"]
    L = [f"Evidence log: {log_path}", f"Total events: {summary['total_events']}", ""]
    if not r["precheck_events"]:
        L.append("No precheck events recorded yet — run the pipeline to accumulate data.")
        L.append("(Corpus-derived number lives in eval/results/codeguard.json.)")
        return "\n".join(L)

    L.append(
        f"Production resolution rate: {r['rate_pct']}% "
        f"({r['validation_clean']}/{r['precheck_events']} prechecks validation-clean)"
    )
    L.append(
        f"Codeguard modified the scene in {r['change_rate_pct']}% of prechecks "
        f"({r['code_changed']}/{r['precheck_events']})"
    )
    L.append("")
    if summary["rules_fired"]:
        L.append("Top fix rules by firing count:")
        for rule, n in list(summary["rules_fired"].items())[:15]:
            L.append(f"  {n:5d}  {rule}")
        L.append("")
    if summary["blocking_errors"]:
        L.append("Blocking errors that survived repair:")
        for err, n in list(summary["blocking_errors"].items())[:10]:
            L.append(f"  {n:5d}  {err}")
        L.append("")
    s = summary["shadow"]
    L.append(
        f"Shadow: {s['unknown_symbol_events']} unknown-symbol event(s), "
        f"{s['invalid_kwarg_events']} invalid-kwarg event(s)"
    )
    a = summary["av_mismatch"]
    L.append(f"A/V mismatches: {a['count']} ({a['large']} large)")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default=None, help="path to codeguard_events.jsonl")
    ap.add_argument("--json", dest="json_out", default=None,
                    help="also write the summary as JSON here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    path = args.log or evidence_path()
    summary = aggregate(read_events(path))

    if args.json_out:
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, "w") as f:
            json.dump(summary, f, indent=2)
            f.write("\n")

    if not args.quiet:
        print(_render(summary, path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
