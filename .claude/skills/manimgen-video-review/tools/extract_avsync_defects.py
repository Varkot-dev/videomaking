"""
Extract A/V sync defects from muxer logs.

The muxer (ffmpeg) emits warnings and errors during muxing that indicate timing
mismatches between audio and video. This tool parses muxer logs to extract:

  - Stream duration mismatches (video N seconds, audio M seconds)
  - Codec parameter mismatches (sample rate, channels)
  - Sync drift warnings (frames arriving out of order)
  - Truncation warnings (streams cut short during mux)

These are synthesized into defects that can be added to an audit.json for later
review. A/V sync issues always result in FAIL status — they cause freeze-frames,
audio leading/lagging video, or dropped frames.

Output: list of defects ready to merge into audit sections.

Usage:
  python3 extract_avsync_defects.py \\
      --muxer-log /tmp/manimgen_review/muxer.log \\
      --out /tmp/manimgen_review/avsync_defects.json

Schema:
{
  "defects": [
    {
      "category": "av_sync",
      "detail": "Video stream (10.5s) is 0.3s longer than audio stream (10.2s); will cause playback trim",
      "source": "muxer_log",
      "severity": "high|medium|low"
    },
    ...
  ],
  "sync_ok": bool,
  "message": "human-readable summary"
}
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any


def parse_muxer_log(log_path: str) -> list[dict[str, Any]]:
    """Parse ffmpeg muxer log and extract A/V sync signals."""
    if not os.path.isfile(log_path):
        return []

    defects = []
    with open(log_path) as f:
        content = f.read()

    # Pattern 1: Stream duration mismatch
    # Example: "Stream durations differ by 0.253000 seconds"
    duration_match = re.search(
        r"Stream durations differ by ([\d.]+) seconds?",
        content,
        re.IGNORECASE
    )
    if duration_match:
        diff = float(duration_match.group(1))
        severity = "high" if diff > 0.5 else "medium" if diff > 0.2 else "low"
        defects.append({
            "category": "av_sync",
            "detail": f"Video and audio stream durations differ by {diff:.3f}s; may cause playback trim or padding",
            "source": "muxer_log",
            "severity": severity,
        })

    # Pattern 2: Codec mismatch (sample rate, channels, etc)
    # Example: "Invalid audio stream. Exactly one audio stream is required."
    # or: "Sample rates differ"
    if re.search(r"Invalid audio stream|Sample rates differ|Channel count mismatch", content, re.IGNORECASE):
        defects.append({
            "category": "av_sync",
            "detail": "Audio stream codec parameters mismatch (sample rate, channels, or format); mux may fail or produce sync errors",
            "source": "muxer_log",
            "severity": "high",
        })

    # Pattern 3: Sync drift or out-of-order frames
    # Example: "DTS ... < last DTS ..."
    if re.search(r"DTS.*<.*last DTS|out of order|sync.*drift", content, re.IGNORECASE):
        defects.append({
            "category": "av_sync",
            "detail": "Frame timing out of order (DTS violation); video will stutter or skip frames",
            "source": "muxer_log",
            "severity": "high",
        })

    # Pattern 4: Stream truncation
    # Example: "Stream ends before any packets"
    # or: "Truncating packet of size XXX to YYY"
    if re.search(r"Stream ends before|Truncating packet|truncated", content, re.IGNORECASE):
        defects.append({
            "category": "av_sync",
            "detail": "Stream truncated during mux; video or audio cut short",
            "source": "muxer_log",
            "severity": "high",
        })

    return defects


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--muxer-log", required=True, help="ffmpeg muxer log file path")
    p.add_argument("--out", required=True, help="output JSON path")
    args = p.parse_args()

    defects = parse_muxer_log(args.muxer_log)
    sync_ok = not defects
    summary = (
        "No A/V sync issues detected"
        if sync_ok
        else f"{len(defects)} A/V sync issue(s) found"
    )

    result = {
        "defects": defects,
        "sync_ok": sync_ok,
        "message": summary,
    }

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    return 0 if sync_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
