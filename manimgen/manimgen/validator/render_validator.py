"""
Render validator — unified post-render quality gate.

Runs after every successful manimgl render (first attempt and retries alike).
Combines frame_checker (zero-cost PIL) and layout_checker (LLM vision) into a
single interface so callers don't need to wire them separately.

Severity:
  "hard" — ok=False: black screen, frozen animation.
           Caller must block muxing and trigger retry.
  "soft" — ok=True, issues non-empty: layout overlaps, edge clipping.
           Caller logs issues and injects them into the next LLM retry prompt.
  "none" — ok=True, issues empty: fully clean.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Literal

from manimgen.validator.frame_checker import check_frames
from manimgen.validator.layout_checker import check_layout

logger = logging.getLogger(__name__)

# frame_checker issue keywords that classify as hard failures.
_HARD_KEYWORDS = ("Black/empty frame", "identical — animation appears frozen")  # black + frozen


@dataclass
class ValidationResult:
    ok: bool
    issues: list[str]
    severity: Literal["hard", "soft", "none"]


def validate_render(
    video_path: str,
    code: str,
    scene_path: str,
    cue_durations: list[float] | None,
) -> ValidationResult:
    """Run post-render visual validation on a successfully rendered video.

    Args:
        video_path:    Path to the rendered .mp4 from manimgl.
        code:          Python source that produced this render (reserved for
                       future static checks — not inspected here).
        scene_path:    Path to the .py scene file (reserved for future use).
        cue_durations: Per-cue durations from TTS segmenter. When None, TTS
                       is disabled and layout_checker is skipped (saves tokens).

    Returns:
        ValidationResult with ok, issues, and severity.
    """
    if not os.path.exists(video_path):
        logger.debug("[render_validator] Video not found, skipping: %s", video_path)
        return ValidationResult(ok=True, issues=[], severity="none")

    all_issues: list[str] = []
    has_hard_failure = False

    # --- Tier 1: frame_checker (zero cost, always runs) ---
    frame_result = check_frames(video_path)
    if not frame_result.ok and not frame_result.skipped:
        for issue in frame_result.issues:
            all_issues.append(issue)
            if any(kw in issue for kw in _HARD_KEYWORDS):
                has_hard_failure = True

    # --- Tier 2: layout_checker (LLM vision, only when TTS is on) ---
    if cue_durations is not None:
        layout = check_layout(video_path)
        if not layout.get("skipped") and not layout.get("ok") and layout.get("issues"):
            for line in layout["issues"].splitlines():
                line = line.strip()
                if line:
                    all_issues.append(line)

    if has_hard_failure:
        logger.info(
            "[render_validator] Hard failure(s) in %s: %d issue(s)",
            video_path, len(all_issues),
        )
        return ValidationResult(ok=False, issues=all_issues, severity="hard")

    if all_issues:
        logger.info(
            "[render_validator] Soft issue(s) in %s: %d issue(s)",
            video_path, len(all_issues),
        )
        return ValidationResult(ok=True, issues=all_issues, severity="soft")

    return ValidationResult(ok=True, issues=[], severity="none")
