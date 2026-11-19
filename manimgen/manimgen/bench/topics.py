"""Load and validate the benchmark topic list."""

from __future__ import annotations

from pathlib import Path

import yaml

from manimgen.bench import TOPICS_FILE
from manimgen.planner.lesson_planner import SHORT_ARCHETYPES


def load_topics(path: Path | None = None, only: list[str] | None = None) -> list[dict]:
    data = yaml.safe_load((path or TOPICS_FILE).read_text(encoding="utf-8"))
    topics = data["topics"]
    seen: set[str] = set()
    for t in topics:
        for key in ("slug", "topic", "format"):
            if not t.get(key):
                raise ValueError(f"topic entry missing {key!r}: {t}")
        if t["format"] not in SHORT_ARCHETYPES:
            raise ValueError(
                f"{t['slug']}: format {t['format']!r} not in {SHORT_ARCHETYPES}"
            )
        if t["slug"] in seen:
            raise ValueError(f"duplicate slug {t['slug']!r}")
        seen.add(t["slug"])
    if only:
        unknown = set(only) - seen
        if unknown:
            raise ValueError(f"unknown slug(s): {sorted(unknown)}")
        topics = [t for t in topics if t["slug"] in only]
    return topics
