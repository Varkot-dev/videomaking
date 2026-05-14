"""
Manage per-topic audit baselines.

Instead of a single audit_prior.json, maintain a baselines/ directory keyed by
topic hash. This allows the audit pipeline to:
  1. Compute hash of video (or derive from plan.json topic)
  2. Look up prior audit for that specific topic
  3. Diff current audit against the topic's baseline
  4. Update baseline after successful run

Topic hash is SHA256(topic_string) truncated to 12 hex chars.

Usage:
  python3 manage_baselines.py --list --base /tmp/manimgen_review/baselines

  python3 manage_baselines.py --get --topic "Fourier Series" --base /tmp/manimgen_review/baselines

  python3 manage_baselines.py --set --topic "Fourier Series" --audit /tmp/manimgen_review/audit.json --base /tmp/manimgen_review/baselines

  python3 manage_baselines.py --hash --topic "Fourier Series"
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any


def topic_hash(topic: str) -> str:
    """Compute 12-char SHA256 hash of topic string."""
    return hashlib.sha256(topic.encode()).hexdigest()[:12]


def baseline_path(base_dir: str, topic: str) -> str:
    """Compute baseline file path for a topic."""
    h = topic_hash(topic)
    return os.path.join(base_dir, f"{h}__{topic.replace('/', '_')[:40]}.json")


def get_baseline(base_dir: str, topic: str) -> dict[str, Any] | None:
    """Load baseline audit for a topic, or None if not found."""
    path = baseline_path(base_dir, topic)
    if not os.path.isfile(path):
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"error reading baseline {path}: {e}", file=sys.stderr)
        return None


def set_baseline(base_dir: str, topic: str, audit: dict[str, Any]) -> str:
    """Write or overwrite baseline for a topic. Returns the path."""
    os.makedirs(base_dir, exist_ok=True)
    path = baseline_path(base_dir, topic)
    with open(path, "w") as f:
        json.dump(audit, f, indent=2)
    return path


def list_baselines(base_dir: str) -> list[dict[str, Any]]:
    """List all baseline files in base_dir."""
    if not os.path.isdir(base_dir):
        return []
    baselines = []
    for fname in sorted(os.listdir(base_dir)):
        if fname.endswith(".json"):
            fpath = os.path.join(base_dir, fname)
            try:
                with open(fpath) as f:
                    audit = json.load(f)
                # Extract topic from filename (format: {hash}__{topic_slug}.json)
                parts = fname.rstrip(".json").split("__", 1)
                h = parts[0]
                topic_slug = parts[1] if len(parts) > 1 else "(unknown)"
                baselines.append({
                    "hash": h,
                    "topic_slug": topic_slug,
                    "path": fpath,
                    "sections": len(audit.get("sections", [])),
                    "pass_count": audit.get("pass_count", "?"),
                    "fail_count": audit.get("fail_count", "?"),
                })
            except (OSError, json.JSONDecodeError):
                pass
    return baselines


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base", default="/tmp/manimgen_review/baselines",
                   help="baseline directory")
    p.add_argument("--list", action="store_true", help="list all baselines")
    p.add_argument("--get", action="store_true", help="get baseline for topic")
    p.add_argument("--set", action="store_true", help="set baseline for topic")
    p.add_argument("--hash", action="store_true", help="print hash of topic")
    p.add_argument("--topic", help="topic string")
    p.add_argument("--audit", help="audit.json path")

    args = p.parse_args()

    if args.list:
        baselines = list_baselines(args.base)
        if not baselines:
            print("no baselines found")
            return 0
        print(f"{'Hash':<12} {'Sections':<9} {'Pass':<5} {'Fail':<5} Topic")
        print("-" * 70)
        for b in baselines:
            print(f"{b['hash']:<12} {b['sections']:<9} {b['pass_count']:<5} {b['fail_count']:<5} {b['topic_slug']}")
        return 0

    elif args.get:
        if not args.topic:
            print("--get requires --topic", file=sys.stderr)
            return 2
        baseline = get_baseline(args.base, args.topic)
        if not baseline:
            print(f"no baseline for topic: {args.topic}", file=sys.stderr)
            return 1
        print(json.dumps(baseline, indent=2))
        return 0

    elif args.set:
        if not args.topic or not args.audit:
            print("--set requires --topic and --audit", file=sys.stderr)
            return 2
        if not os.path.isfile(args.audit):
            print(f"audit file not found: {args.audit}", file=sys.stderr)
            return 1
        with open(args.audit) as f:
            audit = json.load(f)
        path = set_baseline(args.base, args.topic, audit)
        print(f"baseline set: {path}")
        return 0

    elif args.hash:
        if not args.topic:
            print("--hash requires --topic", file=sys.stderr)
            return 2
        h = topic_hash(args.topic)
        print(h)
        return 0

    else:
        p.print_help()
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
