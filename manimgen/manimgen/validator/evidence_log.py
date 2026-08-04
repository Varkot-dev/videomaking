"""Append-only JSONL evidence log for Codeguard / muxer instrumentation.

Purpose
-------
Codeguard's shadow checks and the muxer's A/V mismatch log have always been
in-memory only: `_shadow_log_unknown_symbols` / `_shadow_log_invalid_kwargs`
emitted a `logging.info` line and `_mismatch_log` was a module-level list the
CLI printed at the end of a run. Nothing survived the process, so there was no
accumulating evidence anyone could point at — which is exactly why the
"resolution rate" number had no measurement behind it.

This module persists those events as JSONL so real production runs accumulate
data that can confirm or refute the corpus-derived number
(`eval/results/codeguard.json`).

Design constraints
------------------
- **Off the hot path.** One buffered append per event, no fsync, no read-back.
- **Never fatal.** Instrumentation must not be able to break a render, so every
  write is wrapped: any OSError is swallowed after a single debug line.
- **Opt-outable.** Set `MANIMGEN_EVIDENCE_LOG=0` to disable, or point
  `MANIMGEN_EVIDENCE_DIR` somewhere else. Default: `output/logs/`.
- **Append-only.** Events are never rewritten, so concurrent runs interleave
  safely (each line is a single small write).
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DIR = os.path.join("output", "logs")
_FILENAME = "codeguard_events.jsonl"

# Cap a single event's serialized size so a pathological scene cannot write a
# multi-megabyte line into the log.
_MAX_EVENT_CHARS = 8000


def evidence_enabled() -> bool:
    """Instrumentation is on unless explicitly disabled."""
    return os.environ.get("MANIMGEN_EVIDENCE_LOG", "1").lower() not in (
        "0",
        "false",
        "no",
    )


def evidence_path() -> str:
    """Absolute-or-relative path of the JSONL log for this process."""
    base = os.environ.get("MANIMGEN_EVIDENCE_DIR", _DEFAULT_DIR)
    return os.path.join(base, _FILENAME)


def log_event(event_type: str, **fields: Any) -> bool:
    """Append one event to the evidence log. Returns True if it was written.

    Never raises: instrumentation must not be able to fail a render.
    """
    if not evidence_enabled():
        return False

    record = {"ts": round(time.time(), 3), "event": event_type, **fields}
    try:
        line = json.dumps(record, default=str)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        logger.debug("[evidence] could not serialize event %s", event_type)
        return False

    if len(line) > _MAX_EVENT_CHARS:
        record = {
            "ts": record["ts"],
            "event": event_type,
            "truncated": True,
            "original_chars": len(line),
        }
        line = json.dumps(record)

    path = evidence_path()
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a") as f:
            f.write(line + "\n")
        return True
    except OSError as exc:
        logger.debug("[evidence] write failed (%s): %s", path, exc)
        return False


def read_events(path: str | None = None) -> list[dict[str, Any]]:
    """Read back every well-formed event. Malformed lines are skipped.

    Used by the aggregator (`eval/aggregate_logs.py`). Not called on the hot path.
    """
    target = path or evidence_path()
    if not os.path.exists(target):
        return []
    events: list[dict[str, Any]] = []
    with open(target) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # tolerate a torn final line from a killed process
            if isinstance(rec, dict):
                events.append(rec)
    return events
