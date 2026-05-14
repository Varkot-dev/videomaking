"""
Diff a current audit.json against a prior audit.json and surface regressions.

A "regression" is any of:
  - A section that went from PASS → FAIL (or → FALLBACK)
  - A new defect category appearing in a section that didn't have it before
  - codeguard_warnings_total increased

An "improvement" is the inverse: FAIL → PASS, defect category disappeared, or
codeguard total decreased.

Output exit code is non-zero if any regressions exist — useful for CI gating.

Usage:
  python3 diff_audit.py --current /tmp/manimgen_review/audit.json \\
                        --prior  /tmp/manimgen_review/audit_prior.json
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _by_id(audit: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {sec["id"]: sec for sec in audit.get("sections", [])}


def _defect_categories(sec: dict[str, Any]) -> set[str]:
    return {d.get("category", "") for d in sec.get("defects", [])}


def diff_audits(current: dict[str, Any], prior: dict[str, Any]) -> dict[str, Any]:
    cur_secs = _by_id(current)
    pri_secs = _by_id(prior)

    regressions: list[str] = []
    improvements: list[str] = []

    all_ids = sorted(set(cur_secs) | set(pri_secs))
    for sec_id in all_ids:
        cur = cur_secs.get(sec_id)
        pri = pri_secs.get(sec_id)
        if cur is None:
            improvements.append(f"section {sec_id} removed")
            continue
        if pri is None:
            # New section. Treat presence as informational, not a regression.
            if cur.get("status") != "PASS":
                regressions.append(
                    f"section {sec_id} is new and failing ({cur.get('status')})"
                )
            continue

        cur_status = cur.get("status")
        pri_status = pri.get("status")
        if cur_status != pri_status:
            if cur_status == "PASS":
                improvements.append(
                    f"section {sec_id} {pri_status} → PASS"
                )
            else:
                regressions.append(
                    f"section {sec_id} {pri_status} → {cur_status}"
                )

        cur_cats = _defect_categories(cur)
        pri_cats = _defect_categories(pri)
        new_cats = cur_cats - pri_cats
        gone_cats = pri_cats - cur_cats
        for cat in sorted(new_cats):
            regressions.append(
                f"section {sec_id}: new defect category '{cat}'"
            )
        for cat in sorted(gone_cats):
            improvements.append(
                f"section {sec_id}: defect category '{cat}' resolved"
            )

    cur_cg = current.get("codeguard_warnings_total", 0)
    pri_cg = prior.get("codeguard_warnings_total", 0)
    if cur_cg > pri_cg:
        regressions.append(
            f"codeguard warnings increased: {pri_cg} → {cur_cg}"
        )
    elif cur_cg < pri_cg:
        improvements.append(
            f"codeguard warnings decreased: {pri_cg} → {cur_cg}"
        )

    return {
        "regressions": regressions,
        "improvements": improvements,
        "regression_count": len(regressions),
        "improvement_count": len(improvements),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--current", required=True)
    p.add_argument("--prior", required=True)
    args = p.parse_args()

    try:
        with open(args.current) as f:
            cur = json.load(f)
        with open(args.prior) as f:
            pri = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error reading audit files: {exc}", file=sys.stderr)
        return 2

    diff = diff_audits(cur, pri)
    print(json.dumps(diff, indent=2))
    return 1 if diff["regression_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
