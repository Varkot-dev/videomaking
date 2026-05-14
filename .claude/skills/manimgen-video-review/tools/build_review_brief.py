"""
Build a single Markdown review brief from frames.json + auto_classify output.

Instead of Claude opening 24+ separate Read calls, this produces ONE markdown
file that references each frame inline (Read can pick up the JPEGs by path)
and groups them by section, with the auto-classifier's verdict at the top of
each section so review attention focuses on NEEDS_REVIEW items.

Output:
  /tmp/manimgen_review/review_brief.md

Schema:
  # Review Brief — <video_name>
  Summary: N FAIL, M NEEDS_REVIEW, ...

  ## Section 1 — <title> [STATUS]
  Auto-defects: ...
  Frames:
   - frame_X.Xs.jpg (open)
   - frame_X.Xs.jpg (mid)
   - ...

Usage:
  python3 build_review_brief.py \\
      --frames /tmp/manimgen_review/frames.json \\
      --audit  /tmp/manimgen_review/audit_auto.json \\
      --out    /tmp/manimgen_review/review_brief.md
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict


def build_brief(frames_path: str, audit_path: str) -> str:
    with open(frames_path) as f:
        frames_meta = json.load(f)
    with open(audit_path) as f:
        audit = json.load(f)

    video = audit.get("video_path", "(unknown)")
    sections = audit.get("sections", [])
    by_id = {s["id"]: s for s in sections}

    grouped: dict[int | None, list[dict]] = defaultdict(list)
    for fr in frames_meta.get("frames", []):
        grouped[fr.get("section_id")].append(fr)

    lines: list[str] = []
    lines.append(f"# Review Brief — `{os.path.basename(video)}`")
    lines.append("")
    lines.append(f"**Summary:** {audit.get('summary', 'no summary')}")
    lines.append(f"**Codeguard warnings:** {audit.get('codeguard_warnings_total', 0)}")
    lines.append(f"**Video duration:** {frames_meta.get('video_duration_s', '?')}s")
    lines.append("")

    if grouped.get(None):
        lines.append("## Bookend frames (intro / outro)")
        for fr in sorted(grouped[None], key=lambda x: x.get("timestamp_s", 0)):
            phase = fr.get("phase", "?")
            ts = fr.get("timestamp_s", "?")
            path = fr.get("frame_path", "?")
            lines.append(f"- [{phase} @ {ts}s] `{path}`")
        lines.append("")

    for sec_id in sorted(s for s in grouped if s is not None):
        sec = by_id.get(sec_id, {})
        title = sec.get("title", f"Section {sec_id}")
        status = sec.get("status", "NEEDS_REVIEW")
        lines.append(f"## Section {sec_id} — {title} [{status}]")

        defects = sec.get("defects", [])
        if defects:
            lines.append("**Auto-defects:**")
            for d in defects:
                cat = d.get("category", "?")
                detail = d.get("detail", "")
                lines.append(f"- `{cat}` — {detail}")
        else:
            lines.append("_No auto-detected defects. Visual review required to confirm PASS._")
        lines.append("")
        lines.append("**Frames** (open → mid → close):")
        sec_frames = sorted(
            grouped[sec_id],
            key=lambda x: (
                {"open": 0, "mid": 1, "close": 2}.get(x.get("phase", ""), 3),
                x.get("timestamp_s", 0),
            ),
        )
        for fr in sec_frames:
            phase = fr.get("phase", "?")
            ts = fr.get("timestamp_s", "?")
            path = fr.get("frame_path", "?")
            lines.append(f"- [{phase} @ {ts}s] `{path}`")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--frames", required=True)
    p.add_argument("--audit", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    if not os.path.isfile(args.frames):
        print(f"frames.json not found: {args.frames}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.audit):
        print(f"audit.json not found: {args.audit}", file=sys.stderr)
        return 1

    brief = build_brief(args.frames, args.audit)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        f.write(brief)
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
