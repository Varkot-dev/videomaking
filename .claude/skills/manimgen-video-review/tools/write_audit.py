"""
Write a structured audit result for a manimgen render.

Schema is fixed so future runs can diff against prior audits and detect
regressions without re-parsing prose. Claude (in the video-review skill)
calls this after reviewing all frames, passing in per-section verdicts.

Usage from a script:
    python3 write_audit.py --out /tmp/manimgen_review/audit.json \\
        --video /path/to/video.mp4 \\
        --section '1:PASS' \\
        --section '2:FAIL:title-zone collision: scene_title and geo_title both at top edge' \\
        --section '3:PASS'

Or programmatically:
    from write_audit import write_audit
    write_audit(out_path, {"video_path": ..., "sections": [...]})

Schema (audit.json):
{
  "schema_version": 1,
  "video_path": "/abs/path/to/video.mp4",
  "audited_at": "2026-04-25T04:36:00Z",
  "sections": [
    {
      "id": 1,
      "title": "What Does it Mean to 'Align'?",
      "status": "PASS" | "FAIL" | "FALLBACK",
      "defects": [
        {"category": "title_zone_collision", "frame": "frame_18.1s.jpg",
         "detail": "scene_title and geo_title both at top edge"},
        ...
      ]
    },
    ...
  ],
  "codeguard_warnings_total": 0,
  "fail_count": 0,
  "pass_count": 6
}

Defect categories (from skill checklist):
  title_zone_collision  — A (layout)
  text_overlap          — B (layout)
  edge_clipping         — C (layout)
  fallback              — D (content, full-section fallback)
  freeze                — D (content, multi-frame identical)
  layout_proportion     — E (layout)
  codeguard             — static analysis (emitted by codeguard_audit.py)
  av_sync               — A/V sync mismatch (emitted by extract_avsync_defects.py)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from typing import Any


VALID_STATUSES = {"PASS", "FAIL", "FALLBACK"}
VALID_CATEGORIES = {
    "title_zone_collision",
    "text_overlap",
    "edge_clipping",
    "fallback",
    "freeze",
    "layout_proportion",
    "codeguard",
    "av_sync",
}


def write_audit(out_path: str, audit: dict[str, Any]) -> dict[str, Any]:
    """Validate, normalize, and write audit JSON. Returns the written object."""
    audit.setdefault("schema_version", 1)
    audit.setdefault("audited_at", dt.datetime.utcnow().isoformat(timespec="seconds") + "Z")

    sections = audit.get("sections", [])
    if not isinstance(sections, list):
        raise ValueError("audit.sections must be a list")
    fail = 0
    pas = 0
    for sec in sections:
        status = sec.get("status")
        if status not in VALID_STATUSES:
            raise ValueError(f"section {sec.get('id')} has invalid status: {status!r}")
        if status == "PASS":
            pas += 1
        else:
            fail += 1
        for defect in sec.get("defects", []):
            cat = defect.get("category")
            if cat not in VALID_CATEGORIES:
                raise ValueError(f"section {sec.get('id')} has invalid defect category: {cat!r}")

    audit["fail_count"] = fail
    audit["pass_count"] = pas

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(audit, f, indent=2)
    return audit


def _parse_section_arg(s: str) -> dict[str, Any]:
    """Parse '1:PASS' or '2:FAIL:title-zone collision: details'."""
    parts = s.split(":", 2)
    if len(parts) < 2:
        raise ValueError(f"section arg malformed: {s!r}")
    sec_id = int(parts[0])
    status = parts[1].strip().upper()
    sec: dict[str, Any] = {"id": sec_id, "status": status, "defects": []}
    if len(parts) == 3 and parts[2].strip():
        # Heuristic category extraction from the front of the detail string.
        detail = parts[2].strip()
        detail_lower = detail.lower()
        cat = "title_zone_collision" if "title" in detail_lower else (
              "text_overlap" if "overlap" in detail_lower else (
              "edge_clipping" if "clip" in detail_lower else (
              "fallback" if "fallback" in detail_lower else (
              "freeze" if "froz" in detail_lower or "freeze" in detail_lower else (
              "av_sync" if "sync" in detail_lower or "a/v" in detail_lower else
              "layout_proportion")))))
        sec["defects"].append({"category": cat, "detail": detail})
    return sec


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--section", action="append", default=[],
                   help="ID:STATUS or ID:STATUS:DETAIL — repeat per section")
    p.add_argument("--codeguard-warnings-total", type=int, default=0)
    args = p.parse_args()

    sections = [_parse_section_arg(s) for s in args.section]
    audit = {
        "video_path": args.video,
        "sections": sections,
        "codeguard_warnings_total": args.codeguard_warnings_total,
    }
    written = write_audit(args.out, audit)
    print(json.dumps(written, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
