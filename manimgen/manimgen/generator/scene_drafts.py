"""Write several drafts of a short's scene, keep the best one.

The scene-writing model sometimes produces a weak scene, and repairing it costs
minutes of re-rendering. For a short, the Director is asked for ``count``
independent scenes at the same time. Each draft goes through the same per-draft
steps a single scene gets (codeguard, safety gate, timing gate) in its own
file, the survivors are rendered small (540x960) in parallel, and the checks
that cost nothing score them. The best draft is copied to the section's normal
scene path and the pipeline continues from there exactly as it does for a
single scene: the full-size render, the retry loop and the fallback all apply.
The draft videos are only used for scoring and are deleted afterwards.

Scoring (lower is better, compared as a tuple):

1. A draft that cannot be judged is the worst: it was rejected before
   rendering (precheck, safety gate, unresolved timing issues), its render
   failed or timed out, or the model call itself failed.
2. The number of hard issues: findings of the render probe (text over text,
   text outside the safe area), blocking freeze-frame tails from the timing
   verifier, and hard frame checks (black or frozen frames).
3. The number of timing warnings from the timing verifier.
4. Shorter code.

If no draft can be judged, the first draft's code goes on the normal path as
it is, so the usual retry and fallback still apply.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from manimgen import paths, timing
from manimgen.types import GateResult
from manimgen.utils import (
    draft_class_name,
    is_usage_stop,
    rename_scene_class,
    safe_section_id,
    section_class_name,
)

logger = logging.getLogger(__name__)

DRAFTS_LOG_NAME = "drafts.jsonl"
_log_lock = threading.Lock()

_VARIANTS = "ABC"


@dataclass
class Draft:
    """One draft and what became of it."""

    variant: str
    gate: GateResult | None = None
    error: BaseException | None = None
    failure: str = ""  # why it cannot be judged ("" when it was scored)
    hard_issues: int = 0
    timing_warnings: int = 0
    code_chars: int = 0
    video: str | None = None
    details: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.failure)

    @property
    def sort_key(self) -> tuple:
        return (
            1 if self.failed else 0,
            self.hard_issues,
            self.timing_warnings,
            self.code_chars,
        )

    def score_text(self) -> str:
        if self.failed:
            return f"failed ({self.failure})"
        return f"{self.hard_issues}/{self.timing_warnings}/{self.code_chars}"


def _generate_one(variant: str, gate_one: Callable[[str], GateResult]) -> Draft:
    """Write one draft; an exception is kept on the draft, never raised."""
    draft = Draft(variant)
    try:
        draft.gate = gate_one(variant)
    except Exception as exc:  # noqa: BLE001 - judged by the caller
        draft.error = exc
        draft.failure = f"scene writing failed: {type(exc).__name__}"
        logger.warning("[drafts] draft %s could not be written: %s", variant, exc)
        return draft
    draft.code_chars = len(draft.gate.code)
    if draft.gate.precheck_blocked:
        draft.failure = "rejected before rendering"
    elif draft.gate.timing_blocked:
        draft.failure = "timing issues the auto-fix could not resolve"
    return draft


def _render_one(draft: Draft, cue_durations: list[float] | None) -> None:
    """Render a draft small and fill in its scores (or its failure)."""
    from manimgen.validator.render_command import draft_scene_timeout, run_manimgl

    gate = draft.gate
    assert gate is not None
    try:
        with timing.context(f"draft render {draft.variant}"):
            result = run_manimgl(
                gate.scene_path,
                gate.class_name,
                timeout=draft_scene_timeout(gate.scene_path),
                draft=True,
            )
    except Exception as exc:  # noqa: BLE001 - a broken render is a failed draft
        draft.failure = f"render raised {type(exc).__name__}"
        logger.warning("[drafts] draft %s render raised: %s", draft.variant, exc)
        return
    if not result.ok or not result.video_path:
        draft.failure = "render timed out" if result.timed_out else "render failed"
        tail = (result.stderr or "").strip().splitlines()[-1:]  # last stderr line
        if tail:
            draft.details.append(tail[0][:200])
        return
    draft.video = result.video_path
    try:
        _score_render(draft, result, cue_durations)
    except Exception as exc:  # noqa: BLE001 - scoring must not sink the section
        draft.failure = f"scoring raised {type(exc).__name__}"
        logger.warning("[drafts] draft %s scoring raised: %s", draft.variant, exc)


def _score_render(draft: Draft, result, cue_durations: list[float] | None) -> None:
    from manimgen.probes import overlap_report
    from manimgen.validator.frame_checker import check_frames
    from manimgen.validator.render_validator import _HARD_KEYWORDS
    from manimgen.validator.timing_verifier import (
        blocking_freezes,
        join_frozen_with_timing,
        verify_timing,
    )

    gate = draft.gate
    assert gate is not None and draft.video is not None

    timing = verify_timing(gate.code, cue_durations) if cue_durations else {}
    freezes = blocking_freezes(timing) if cue_durations else []
    draft.timing_warnings = len(timing.get("warnings", []))

    frame = check_frames(draft.video)
    frame_hard: list[str] = []
    if not frame.ok and not frame.skipped:
        joined = join_frozen_with_timing(
            frame.issues, timing_freeze_confirmed=bool(freezes)
        )
        frame_hard = [i for i in joined if any(k in i for k in _HARD_KEYWORDS)]

    overlaps = list(result.overlaps or ())
    draft.hard_issues = len(overlaps) + len(freezes) + len(frame_hard)
    draft.details.extend(overlap_report.overlap_issues(overlaps)[:3])
    draft.details.extend(freezes[:2])
    draft.details.extend(frame_hard[:2])


def _remove_draft_files(draft: Draft) -> None:
    """Delete a draft's video and the probe report saved next to it."""
    if not draft.video:
        return
    for path in (draft.video, draft.video + ".overlaps.json"):
        try:
            os.remove(path)
        except OSError:
            pass


def _promote(section: dict, winner: Draft) -> GateResult:
    """Copy the winner to the section's normal scene path under its own name."""
    gate = winner.gate
    assert gate is not None
    final_class = section_class_name(section)
    code = rename_scene_class(
        gate.code, draft_class_name(section, winner.variant), final_class
    )
    os.makedirs(paths.scenes_dir(), exist_ok=True)
    scene_path = os.path.join(paths.scenes_dir(), f"{safe_section_id(section)}.py")
    with open(scene_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(code)
    return GateResult(
        code=code,
        class_name=final_class,
        scene_path=scene_path,
        timing_blocked=gate.timing_blocked,
        precheck_blocked=gate.precheck_blocked,
    )


def _record(section_id: str, drafts: list[Draft], picked: Draft, all_failed: bool):
    """One summary line in the run log and one record in drafts.jsonl."""
    parts = ", ".join(f"{d.variant} score={d.score_text()}" for d in drafts)
    note = " (all drafts failed, using it unchanged)" if all_failed else ""
    logger.info(
        "[drafts] %s: %s, picked %s%s (score is hard issues/timing warnings/code chars)",
        section_id,
        parts,
        picked.variant,
        note,
    )
    for d in drafts:
        for line in d.details:
            logger.debug("[drafts] %s draft %s: %s", section_id, d.variant, line)
    record = {
        "section": section_id,
        "picked": picked.variant,
        "all_failed": all_failed,
        "drafts": [
            {
                "variant": d.variant,
                "failed": d.failed,
                "failure": d.failure or None,
                "hard_issues": None if d.failed else d.hard_issues,
                "timing_warnings": None if d.failed else d.timing_warnings,
                "code_chars": d.code_chars,
                "details": d.details,
            }
            for d in drafts
        ],
    }
    try:
        with _log_lock:
            os.makedirs(paths.logs_dir(), exist_ok=True)
            with open(
                os.path.join(paths.logs_dir(), DRAFTS_LOG_NAME),
                "a",
                encoding="utf-8",
                newline="\n",
            ) as f:
                f.write(json.dumps(record, ensure_ascii=True) + "\n")
    except OSError as exc:
        logger.warning("[drafts] could not write %s: %s", DRAFTS_LOG_NAME, exc)


def pick_best_draft(
    section: dict,
    cue_durations: list[float] | None,
    gate_one: Callable[[str], GateResult],
    count: int,
) -> GateResult:
    """Write ``count`` drafts at once, render and score them, return the best.

    ``gate_one(variant)`` writes one draft and runs its pre-render checks
    (cli._generate_and_gate with a variant). The returned GateResult is the
    winner already copied to the section's normal scene path, so the caller
    carries on as for a single scene.

    A usage-limit stop from any draft is raised at once (the run must stop).
    If every draft raised, the first error is raised, as for a single scene.
    """
    count = max(1, min(count, len(_VARIANTS)))
    variants = list(_VARIANTS[:count])
    section_id = safe_section_id(section)

    with ThreadPoolExecutor(max_workers=count) as pool:
        drafts = list(pool.map(lambda v: _generate_one(v, gate_one), variants))

    for d in drafts:
        if d.error is not None and is_usage_stop(d.error):
            raise d.error
    if all(d.error is not None for d in drafts):
        raise drafts[0].error  # type: ignore[misc]

    renderable = [d for d in drafts if not d.failed]
    if renderable:
        with ThreadPoolExecutor(max_workers=len(renderable)) as pool:
            list(pool.map(lambda d: _render_one(d, cue_durations), renderable))

    try:
        survivors = [d for d in drafts if not d.failed]
        if survivors:
            picked = min(survivors, key=lambda d: d.sort_key)  # ties: earliest
            all_failed = False
        else:
            # Nothing could be judged: the first draft that was written goes on
            # as it is, so the retry loop and the fallback still apply.
            picked = next(d for d in drafts if d.gate is not None)
            all_failed = True
        _record(section_id, drafts, picked, all_failed)
        return _promote(section, picked)
    finally:
        for d in drafts:
            _remove_draft_files(d)
