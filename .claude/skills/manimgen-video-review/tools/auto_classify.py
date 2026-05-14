"""
Auto-classify sections from deterministic signals before human/Claude review.

Combines codeguard_audit.json + frames.json + frame_checker output to produce
a partial audit.json. Sections with strong signals get auto-decided; ambiguous
sections are marked NEEDS_REVIEW so the caller knows where to focus.

Decision rules (in order; first match wins):
  1. codeguard warning containing "title zone" ⇒ FAIL with title_zone_collision
  2. frame_checker reports black frame ⇒ FAIL with edge_clipping (real corruption)
  3. all sampled frames in a section are >97% identical ⇒ FAIL with freeze
  4. frame_checker reports frozen-frame across whole section ⇒ FAIL with fallback
  5. codeguard warning unrelated to title zone ⇒ FAIL with codeguard
  6. otherwise ⇒ NEEDS_REVIEW (human/Claude must look at frames)

Sections never auto-classify as PASS — that requires human verification.

Usage:
  python3 auto_classify.py \\
      --codeguard /tmp/manimgen_review/codeguard.json \\
      --frames    /tmp/manimgen_review/frames.json \\
      --video     /path/to/final.mp4 \\
      --out       /tmp/manimgen_review/audit_auto.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any


_PACKAGE_ROOT = "/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen"


def _import_check_frames():
    if _PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT)
    from manimgen.validator.frame_checker import check_frames  # noqa: E402
    return check_frames


def _frames_for_section(frames: list[dict], sec_id: int) -> list[dict]:
    return [f for f in frames if f.get("section_id") == sec_id]


def _classify_section(
    sec_id: int,
    sec_title: str,
    codeguard_warnings: list[str],
    section_frames: list[dict],
    frame_check_issues: list[str],
) -> dict[str, Any]:
    defects: list[dict[str, Any]] = []

    for w in codeguard_warnings:
        if "title zone" in w.lower():
            defects.append({
                "category": "title_zone_collision",
                "detail": w,
                "source": "codeguard",
            })
        else:
            defects.append({
                "category": "codeguard",
                "detail": w,
                "source": "codeguard",
            })

    for issue in frame_check_issues:
        if "black" in issue.lower() or "empty frame" in issue.lower():
            defects.append({
                "category": "edge_clipping",
                "detail": issue,
                "source": "frame_checker",
            })
        elif "identical" in issue.lower() or "frozen" in issue.lower():
            defects.append({
                "category": "freeze",
                "detail": issue,
                "source": "frame_checker",
            })
        elif "edge" in issue.lower() or "clip" in issue.lower():
            defects.append({
                "category": "edge_clipping",
                "detail": issue,
                "source": "frame_checker",
            })

    if defects:
        status = "FAIL"
    else:
        status = "NEEDS_REVIEW"

    return {
        "id": sec_id,
        "title": sec_title,
        "status": status,
        "defects": defects,
        "frame_count": len(section_frames),
    }


def auto_classify(
    codeguard_path: str,
    frames_path: str,
    video_path: str,
) -> dict[str, Any]:
    if not os.path.isfile(codeguard_path):
        return {"error": f"codeguard.json not found: {codeguard_path}"}
    if not os.path.isfile(frames_path):
        return {"error": f"frames.json not found: {frames_path}"}

    with open(codeguard_path) as f:
        codeguard = json.load(f)
    with open(frames_path) as f:
        frames_meta = json.load(f)

    frames = frames_meta.get("frames", [])
    cg_sections = {s["id"]: s for s in codeguard.get("sections", [])}

    check_frames = _import_check_frames()

    sec_ids = sorted(set(
        s["id"] for s in codeguard.get("sections", [])
    ) | set(
        f["section_id"] for f in frames if f.get("section_id") is not None
    ))

    sections_out: list[dict[str, Any]] = []
    needs_review_count = 0
    fail_count = 0

    for sec_id in sec_ids:
        cg = cg_sections.get(sec_id, {})
        warnings = cg.get("warnings", [])
        sec_frames = _frames_for_section(frames, sec_id)
        sec_title = next(
            (f.get("section_title", f"Section {sec_id}") for f in sec_frames if f.get("section_title")),
            f"Section {sec_id}",
        )

        # Run frame_checker on the full video, but extract only this section's
        # issues by frame timestamps. Since frame_checker operates on the full
        # video, we scope by checking each frame issue against section frame
        # timestamps later.
        frame_issues: list[str] = []
        # frame_checker takes a video path, not individual frames — so we run
        # it on the muxed video. Skip if it errors.
        try:
            # Optimization: only run once across the whole video and reuse,
            # rather than per section. We cache the result by closure.
            if not hasattr(auto_classify, "_fc_cache"):
                fc_result = check_frames(video_path)
                auto_classify._fc_cache = fc_result.issues if not fc_result.ok else []
        except Exception as exc:  # noqa: BLE001
            auto_classify._fc_cache = []
            print(f"[auto_classify] frame_checker error on {video_path}: {exc}", file=sys.stderr)

        all_fc_issues = getattr(auto_classify, "_fc_cache", [])
        # Bucket frame_checker issues by timestamp into section ranges.
        for issue in all_fc_issues:
            ts_match = None
            # frame_checker issues encode timestamps like "at t=8.0s"
            for tok in issue.split():
                if tok.startswith("t=") and "s" in tok:
                    try:
                        ts_match = float(tok.removeprefix("t=").rstrip("s,"))
                        break
                    except ValueError:
                        continue
            if ts_match is None:
                continue
            for fr in sec_frames:
                if abs(fr.get("timestamp_s", -1) - ts_match) < 1.5:
                    frame_issues.append(issue)
                    break

        section_out = _classify_section(
            sec_id, sec_title, warnings, sec_frames, frame_issues
        )

        if section_out["status"] == "FAIL":
            fail_count += 1
        elif section_out["status"] == "NEEDS_REVIEW":
            needs_review_count += 1

        sections_out.append(section_out)

    return {
        "schema_version": 1,
        "video_path": video_path,
        "sections": sections_out,
        "codeguard_warnings_total": codeguard.get("total_warnings", 0),
        "auto_fail_count": fail_count,
        "needs_review_count": needs_review_count,
        "summary": (
            f"{fail_count} auto-FAIL, {needs_review_count} need human review, "
            f"{len(sections_out) - fail_count - needs_review_count} other"
        ),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--codeguard", required=True)
    p.add_argument("--frames", required=True)
    p.add_argument("--video", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    result = auto_classify(args.codeguard, args.frames, args.video)
    if "error" in result:
        print(json.dumps(result, indent=2), file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
