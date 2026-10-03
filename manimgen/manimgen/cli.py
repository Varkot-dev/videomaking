import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable

from manimgen import config, paths
from manimgen.generator.scene_generator import ScenePrecheckError, generate_scenes
from manimgen.input.parser import parse_input
from manimgen.planner.lesson_planner import plan_lesson, plan_lesson_from_pdf
from manimgen.probes import overlap_report
from manimgen.renderer.assembler import assemble_video
from manimgen.renderer.muxer import clear_mismatch_log, get_mismatch_log
from manimgen.types import (
    CueMuxResult,
    GateResult,
    MuxStatus,
    RenderResult,
    SectionOutcome,
    SectionStatus,
)
from manimgen.utils import is_usage_stop, safe_section_id
from manimgen.validator.fallback import fallback_scene
from manimgen.validator.retry import retry_scene
from manimgen.validator.runner import _find_rendered_video, run_scene

logger = logging.getLogger(__name__)

_PLAN_CACHE = paths.plan_cache()


def _load_config() -> dict:
    """The merged settings from the shared loader; a bad config raises ConfigError."""
    return config.load()


def _tts_enabled(cfg: dict) -> bool:
    return cfg.get("tts", {}).get("enabled", False)


def _run_tts_for_section(section: dict, idx: int) -> tuple[str, list, float] | None:
    """Run TTS for a section. Returns (audio_path, timestamps, audio_duration).

    Returns None when the section has no narration text. A TTS failure raises,
    so the caller can record why the section has no voice (#71).
    """
    from manimgen.renderer.tts import (
        check_audio_not_silent,
        generate_narration,
        get_audio_duration,
        save_timestamps,
    )

    narration = section.get("narration", "").strip()
    if not narration:
        return None

    section_id = safe_section_id(section, idx)
    audio_dir = paths.audio_dir()
    os.makedirs(audio_dir, exist_ok=True)
    audio_path = os.path.join(audio_dir, f"{section_id}.mp3")

    logger.info("[manimgen] TTS: %s", section["title"])
    _, timestamps = generate_narration(narration, audio_path)
    ts_path = audio_path.replace(".mp3", "_timestamps.json")
    save_timestamps(timestamps, ts_path)
    audio_duration = get_audio_duration(audio_path)

    energy = check_audio_not_silent(audio_path)
    if not energy["ok"]:
        logger.warning(
            "[manimgen] TTS audio for '%s' is %.0f%% silent — retrying once.",
            section["title"],
            energy["silent_ratio"] * 100,
        )
        _, timestamps = generate_narration(narration, audio_path)
        save_timestamps(timestamps, ts_path)
        audio_duration = get_audio_duration(audio_path)

    logger.info(
        "[manimgen] %d word timestamps, %.1fs audio",
        len(timestamps),
        audio_duration,
    )
    return audio_path, timestamps, audio_duration


def _error_text(exc: BaseException) -> str:
    """One-line description of an exception for the summary and manifest."""
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__


def _muxed_path_for(section: dict, idx: int, cue_index: int) -> str:
    section_id = safe_section_id(section, idx)
    return os.path.join(paths.muxed_dir(), f"{section_id}_cue{cue_index:02d}.mp4")


def _all_cues_muxed(section: dict, idx: int, n_cues: int, key: str) -> bool:
    """True only if every cue clip exists AND was muxed for this content key."""
    return all(
        _render_is_fresh(_muxed_path_for(section, idx, i), key) for i in range(n_cues)
    )


# Per-cue files in the shared muxed folder: <id>_cueNN.mp4 (muxed),
# <id>_cueNN_video.mp4 (silent cut) and their .hash sidecars.
_CUE_FILE_SUFFIX = re.compile(r"_cue\d+(?:_video)?\.mp4(?:\.hash)?")


def _remove_cue_files(
    section_id: str, log: logging.LoggerAdapter | logging.Logger
) -> None:
    """Delete a section's cut/muxed cue files before re-cutting (#66).

    Stale cues from another plan would otherwise linger (a plan with fewer
    cues leaves the old higher-numbered ones behind for manimgen-edit to list).
    """
    muxed_dir = paths.muxed_dir()
    if not os.path.isdir(muxed_dir):
        return
    for name in os.listdir(muxed_dir):
        if not name.startswith(section_id):
            continue
        if not _CUE_FILE_SUFFIX.fullmatch(name[len(section_id) :]):
            continue
        try:
            os.remove(os.path.join(muxed_dir, name))
        except OSError as e:
            log.warning("[manimgen] Could not remove stale cue file %s: %s", name, e)


def _mux_one_cue(
    section: dict,
    idx: int,
    cue_index: int,
    cue_clip: str,
    audio_slice: str,
    mux_fn: Callable[[str, str, str], str],
    log: logging.LoggerAdapter | logging.Logger,
) -> CueMuxResult:
    """Mux one cue's narration onto its video, retrying once on failure.

    Returns a CueMuxResult. A FAILED result NEVER carries the silent ``cue_clip``
    in ``path`` — the caller must drop it rather than ship narration-less video
    (issue #28). On a missing audio slice or a mux that fails even after one
    retry, the failure is logged loudly with the cue index, section, and error.
    """
    section_id = section.get("id", f"section_{idx:02d}")
    muxed = _muxed_path_for(section, idx, cue_index)

    # No "already muxed" shortcut here: an existing file may belong to another
    # plan (#66). Reuse is decided once per section by _all_cues_muxed.
    if not os.path.exists(audio_slice):
        msg = f"audio slice missing: {audio_slice}"
        log.error(
            "[manimgen] Mux FAILED cue %d (section %s): %s — refusing to ship "
            "narration-less clip.",
            cue_index,
            section_id,
            msg,
        )
        return CueMuxResult(cue_index, MuxStatus.FAILED, None, error=msg)

    # First attempt, then exactly one retry before giving up.
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            mux_fn(cue_clip, audio_slice, muxed)
            status = MuxStatus.SUCCESS if attempt == 0 else MuxStatus.RETRIED_OK
            log.info(
                "[manimgen] Muxed cue %d (%s): %s",
                cue_index,
                status.value,
                os.path.basename(muxed),
            )
            return CueMuxResult(cue_index, status, muxed)
        except Exception as e:
            last_error = e
            if attempt == 0:
                log.warning(
                    "[manimgen] Mux failed cue %d (section %s): %s — retrying once.",
                    cue_index,
                    section_id,
                    e,
                )

    msg = str(last_error)
    log.error(
        "[manimgen] Mux FAILED cue %d (section %s) after retry: %s — refusing "
        "to ship narration-less clip.",
        cue_index,
        section_id,
        msg,
    )
    return CueMuxResult(cue_index, MuxStatus.FAILED, None, error=msg)


def _topic_hash(topic_or_pdf: str) -> str:
    """Stable 8-char hash of the input (topic string or pdf path)."""
    return hashlib.sha256(topic_or_pdf.encode()).hexdigest()[:8]


def _content_hash(topic_hash: str, cfg: dict) -> str:
    """Run hash: the input plus everything that changes the files produced.

    Voice and speed change the narration without changing the plan (#66). The
    render quality, resolution and fps change the rendered clips: without them a
    480p draft would be reused as the finished video after switching to full
    quality, which defeats the draft-then-final workflow.
    """
    tts_cfg = cfg.get("tts") or {}
    return _topic_hash(
        json.dumps(
            [
                topic_hash,
                tts_cfg.get("voice"),
                tts_cfg.get("speed"),
                paths.render_quality_flag(),
                paths.render_resolution(),
                paths.render_fps(),
            ]
        )
    )


def _file_hash(path: str) -> str:
    """Stable 8-char hash of a file's bytes (a PDF edited in place must differ)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()[:8]


def _section_key(
    section: dict, topic_hash: str, cue_durations: list[float] | None
) -> str:
    """Content key for every cached per-section artifact (#66).

    Section files live in shared folders named only by section id, and planners
    emit generic ids, so the id alone says nothing about which plan produced a
    file. The key covers the whole section dict (narration, cue indices, cue
    visuals, title), the run hash (topic or PDF bytes plus TTS voice and speed)
    and the cue durations rounded to 0.05s, so small TTS timing jitter on
    --resume still hits the cache while any real change misses it.
    """
    durations = [round(d * 20) for d in cue_durations or []]
    payload = json.dumps([topic_hash, section, durations], sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sidecar_hash_path(video_path: str) -> str:
    """Sidecar file that stores the content key next to a cached video."""
    return video_path + ".hash"


def _render_is_fresh(video_path: str, key: str) -> bool:
    """Return True only if the video exists, is non-empty AND its sidecar
    records this content key. Used for renders and muxed cue clips alike."""
    if not os.path.exists(video_path):
        return False
    if os.path.getsize(video_path) == 0:
        logger.warning(
            "[manimgen] %s is empty; treating as stale", os.path.basename(video_path)
        )
        return False
    sidecar = _sidecar_hash_path(video_path)
    if not os.path.exists(sidecar):
        # Legacy file with no sidecar, treat as stale to be safe
        logger.warning(
            "[manimgen] No .hash sidecar for %s; treating as stale",
            os.path.basename(video_path),
        )
        return False
    stored = _read_sidecar(video_path)[0]
    if stored != key:
        logger.warning(
            "[manimgen] Stale file detected: %s was built for content key %s, current is %s; rebuilding",
            os.path.basename(video_path),
            stored,
            key,
        )
        return False
    return True


def _read_sidecar(video_path: str) -> list[str]:
    """Lines of a sidecar: [key] or [key, status, reason]; [""] if unreadable."""
    try:
        with open(_sidecar_hash_path(video_path), encoding="utf-8") as f:
            lines = f.read().strip().splitlines()
    except (OSError, UnicodeDecodeError):
        return [""]
    return [line.strip() for line in lines] or [""]


def _write_hash_sidecar(
    video_path: str,
    key: str,
    status: SectionStatus = SectionStatus.OK,
    reason: str = "",
) -> None:
    """Record the content key, plus the status when it is not OK (#71).

    A fallback card or a render accepted with defects is still cached, so a
    later --resume must report it as such, not as a clean section.
    """
    text = key
    if status != SectionStatus.OK:
        text += f"\n{status.value}\n{' '.join(reason.split())}"
    sidecar = _sidecar_hash_path(video_path)
    tmp = sidecar + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, sidecar)


def _cached_outcome(video_path: str) -> tuple[SectionStatus, str]:
    """Status and reason recorded with a cached file (OK when none was)."""
    lines = _read_sidecar(video_path)
    try:
        status = SectionStatus(lines[1]) if len(lines) > 1 else SectionStatus.OK
    except ValueError:
        status = SectionStatus.OK
    reason = lines[2] if len(lines) > 2 else ""
    return status, f"cached; {reason}" if reason else "cached"


def _code_blocking_freezes(code: str, cue_durations: list[float]) -> list[str]:
    """Single authoritative freeze-frame gate over a scene's source code.

    Both the fresh-render path and the render-cache fast-path route through this
    one helper so the timing freeze check can never drift between them (#24/#31).
    Returns the list of blocking freeze-frame tails (empty when clean; post-#23
    UNKNOWN/dynamic-duration cues are never reported as freezes).
    """
    from manimgen.validator.timing_verifier import blocking_freezes, verify_timing

    return blocking_freezes(verify_timing(code, cue_durations))


def _cached_scene_blocking_freezes(
    section: dict, cue_durations: list[float]
) -> list[str]:
    """Return blocking freeze-frame tails for a section's CACHED scene source.

    The render-cache / --resume path skips codegen, so it never runs the
    timing freeze gate that retry.py applies. This reads the on-disk scene
    .py (deterministic path: scenes_dir/<section id>.py) and runs the same
    zero-cost static check. Returns [] when the scene file is missing/unreadable
    (fail-open: an unverifiable cache is honored, not the place to hard-fail) or
    when there are no real freezes. Post-#23, cues with dynamic (UNKNOWN)
    durations are never reported as freezes.
    """
    section_id = section.get("id", "")
    if not section_id:
        return []
    scene_path = os.path.join(paths.scenes_dir(), f"{section_id}.py")
    return _scene_file_blocking_freezes(scene_path, cue_durations)


# ---------------------------------------------------------------------------
# Per-section pipeline seams (#31)
#
# _run_section was a 160+ line function doing six jobs; the render-cache
# fast-path bypassed the timing freeze gate that the codegen path runs. It is
# now decomposed into three pure-ish seams with explicit signatures:
#
#   _generate_and_gate  — codegen + zero-cost timing gate  → GateResult
#   _render_with_retry  — first render + validate + retry + fallback → RenderResult
#   _cut_and_mux        — cut into per-cue clips + mux narration → [clip paths]
#
# Behavior is identical to the prior inline version; the cache freeze check and
# the render-path freeze check now both route through _code_blocking_freezes so
# the two can never drift (#24/#31).
# ---------------------------------------------------------------------------


def _generate_and_gate(
    section: dict,
    cue_durations: list[float] | None,
    overview: dict | None,
) -> GateResult:
    """Codegen one scene for the section and run the zero-cost timing gate.

    Generates the scene, then runs the single authoritative timing gate (the
    same pass retry.py uses): verify → auto-fix → re-verify. If timing issues
    survive auto-fix, ``timing_blocked`` is True so the caller skips the
    expensive first render and routes straight to the retry path (which can
    apply an LLM fix with the timing warnings in context). A draft that fails
    codeguard's precheck sets ``precheck_blocked`` and takes the same route.
    """
    try:
        code, class_name, scene_path = generate_scenes(
            section, cue_durations=cue_durations, overview=overview
        )
    except ScenePrecheckError as exc:
        # The draft is on disk. Do not abort the run: skip the doomed render and
        # let retry_scene repair it (error-aware fixes, LLM fix, then fallback).
        logger.warning(
            "[manimgen] Draft failed precheck, skipping first render and "
            "routing to retry: %s",
            exc,
        )
        return GateResult(
            code=exc.code,
            class_name=exc.class_name,
            scene_path=exc.scene_path,
            timing_blocked=False,
            precheck_blocked=True,
        )

    timing_blocked = False
    if cue_durations:
        from manimgen.validator.retry import apply_timing_gate

        code, remaining_timing_warnings = apply_timing_gate(
            code, scene_path, cue_durations
        )
        if remaining_timing_warnings:
            logger.warning(
                "[manimgen] Unresolvable timing issues after auto-fix — "
                "skipping first render, routing to retry: %s",
                "; ".join(remaining_timing_warnings),
            )
            timing_blocked = True

    return GateResult(
        code=code,
        class_name=class_name,
        scene_path=scene_path,
        timing_blocked=timing_blocked,
    )


def _render_with_retry(
    section: dict,
    gate: GateResult,
    cue_durations: list[float] | None,
    log: logging.LoggerAdapter | logging.Logger,
) -> RenderResult:
    """Render a gated scene, forcing the retry/fallback path on any failure.

    The first render is skipped entirely when ``gate.timing_blocked`` or
    ``gate.precheck_blocked`` is set.
    A successful first render is still re-checked for hard visual failures
    (validate_render) and blocking freeze-frame tails — either forces the retry
    path. If retries fail, the styled fallback scene is used. The result says
    which of these shipped (#71); its path is None only if the fallback also
    failed.
    """
    if gate.timing_blocked or gate.precheck_blocked:
        success, video_path = False, None
    else:
        success, video_path = run_scene(gate.scene_path, gate.class_name)

    if success and video_path:
        from manimgen.validator.render_validator import validate_render

        vr = validate_render(video_path, gate.code, gate.scene_path, cue_durations)
        if vr.severity == "hard":
            log.warning(
                "[manimgen] First-pass render has hard failures — forcing retry: %s",
                "; ".join(vr.issues),
            )
            success = False

        # #24: validate_render covers frames/layout but NOT timing freezes.
        # The first-pass render could still ship a multi-second freeze-frame
        # tail. Run the same freeze gate retry.py uses (post-#23: UNKNOWN cues
        # never block) and treat a real freeze as a hard failure → retry path.
        if success and cue_durations:
            freezes = _code_blocking_freezes(gate.code, cue_durations)
            if freezes:
                log.warning(
                    "[manimgen] First-pass render has %d blocking "
                    "freeze-frame tail(s) — forcing retry: %s",
                    len(freezes),
                    "; ".join(freezes),
                )
                success = False

    if success and video_path:
        return RenderResult(video_path, SectionStatus.OK, "first render")

    success, video_path = retry_scene(
        section,
        gate.code,
        gate.class_name,
        gate.scene_path,
        cue_durations=cue_durations,
    )
    if success and video_path:
        # retry_scene accepts a render with known freeze-frame tails on its
        # last attempt; re-run the same zero-cost check on the final source.
        freezes = _scene_file_blocking_freezes(gate.scene_path, cue_durations)
        # #97: the same holds for text overlaps the render probe still sees.
        overlaps = overlap_report.load_for_video(video_path).overlaps
        defects = freezes + [
            f"text overlap {o.a!r} / {o.b!r} at t={o.time:.1f}s" for o in overlaps
        ]
        if defects:
            return RenderResult(
                video_path,
                SectionStatus.ACCEPTED_WITH_DEFECTS,
                "accepted after retries with " + "; ".join(defects),
            )
        return RenderResult(video_path, SectionStatus.OK, "repaired by retry")

    log.warning(
        "[manimgen] Render and all retries failed for %s, using the fallback "
        "title card (see the retry log in %s)",
        gate.class_name,
        paths.logs_dir(),
    )
    video_path = fallback_scene(section)
    if video_path:
        return RenderResult(
            video_path,
            SectionStatus.FALLBACK,
            "render and retries failed; a title card stands in",
        )
    return RenderResult(
        None, SectionStatus.DROPPED, "render, retries and the fallback card failed"
    )


def _scene_file_blocking_freezes(
    scene_path: str, cue_durations: list[float] | None
) -> list[str]:
    """Blocking freeze-frame tails in a scene file on disk ([] if unreadable)."""
    if not cue_durations:
        return []
    try:
        with open(scene_path, encoding="utf-8") as f:
            code = f.read()
    except (OSError, UnicodeDecodeError):
        return []
    return _code_blocking_freezes(code, cue_durations)


def _cut_and_mux(
    section: dict,
    idx: int,
    video_path: str,
    segments: list,
    audio_slices: list[str],
    cue_durations: list[float],
    log: logging.LoggerAdapter | logging.Logger,
    key: str,
    status: SectionStatus = SectionStatus.OK,
    reason: str = "",
) -> list[str]:
    """Cut a rendered section into per-cue clips and mux narration onto each.

    Returns the ordered list of muxed clip paths. If ANY cue fails to mux with
    narration, the whole section is dropped (returns []) and logged loudly — a
    silent clip must never reach the assembler (#28). A FAILED cue's silent
    video is deliberately never appended to the produced list. On success each
    muxed clip gets a ``.hash`` sidecar holding ``key`` (#66) and, when it is
    not OK, the render's status and reason (#71).
    """
    from manimgen.renderer.cutter import (
        cue_start_times_from_durations,
        cut_video_at_cues,
    )
    from manimgen.renderer.muxer import mux_audio_video

    section_id = safe_section_id(section, idx)
    _remove_cue_files(section_id, log)
    cue_starts = cue_start_times_from_durations(cue_durations)
    cue_video_clips = cut_video_at_cues(
        video_path,
        cue_starts,
        cue_durations,
        output_dir=paths.muxed_dir(),
        section_id=section_id,
    )

    produced: list[str] = []
    failed: list[CueMuxResult] = []
    for i, (cue_clip, audio_slice) in enumerate(zip(cue_video_clips, audio_slices)):
        result = _mux_one_cue(
            section, idx, i, cue_clip, audio_slice, mux_audio_video, log
        )
        if result.ok and result.path:
            produced.append(result.path)
        else:
            failed.append(result)

    if failed:
        # A failed cue means a narration-less (silent) clip. We must NOT ship
        # it: the assembler cannot distinguish a silent clip from a real one,
        # so it would incorporate animation with no voice into the final
        # video. Mark the whole section failed and surface it loudly. The
        # silent cue_clip is deliberately never appended to `produced`.
        log.error(
            "[manimgen] Section %d (%s) FAILED: %d/%d cue(s) could not be "
            "muxed with narration — dropping section to avoid shipping "
            "silent video. Failed cues: %s",
            idx,
            section_id,
            len(failed),
            len(cue_video_clips),
            "; ".join(
                f"cue {r.cue_index} ({r.status.value}: {r.error})" for r in failed
            ),
        )
        return []
    for path in produced:
        _write_hash_sidecar(path, key, status, reason)
    return produced


# ---------------------------------------------------------------------------
# Per-section pipeline
# ---------------------------------------------------------------------------


def _build_overview(plan: dict, all_section_audio: dict) -> dict:
    """Build a summary of the lesson plan enriched with TTS audio durations.

    Args:
        plan:              The lesson plan dict (from plan_lesson / plan_lesson_from_pdf).
        all_section_audio: Mapping of section_id → TTS result dict, as returned
                           by _run_tts_for_section (keys: audio_path, timestamps,
                           audio_duration, segments, audio_slices, cue_durations).

    Returns a dict with:
        total_duration, n_sections, pacing_notes, sections (list of per-section summaries).
    """
    sections_summary = []
    total = 0.0
    for i, section in enumerate(plan.get("sections", []), start=1):
        sid = safe_section_id(section, i)
        audio = all_section_audio.get(sid, {})
        dur = audio.get("audio_duration", 0.0)
        cue_durations = audio.get("cue_durations", [])
        total += dur
        sections_summary.append(
            {
                "id": sid,
                "title": section.get("title", ""),
                "position": f"{i} of {len(plan.get('sections', []))}",
                "duration": dur,
                "n_cues": len(cue_durations),
            }
        )

    # Simple pacing note
    avg = total / len(sections_summary) if sections_summary else 0.0
    pacing_notes = f"Total: {total:.1f}s across {len(sections_summary)} sections (avg {avg:.1f}s each)."

    return {
        "total_duration": total,
        "n_sections": len(sections_summary),
        "pacing_notes": pacing_notes,
        "sections": sections_summary,
    }


def _segment_and_slice(
    section: dict,
    tts_result: tuple[str, list, float],
    section_id: str,
) -> tuple[list, list[str]]:
    """Compute cue segments from TTS timestamps and slice the audio file."""
    from manimgen.planner.segmenter import compute_segments
    from manimgen.renderer.audio_slicer import slice_audio

    audio_path, timestamps, audio_duration = tts_result
    cue_word_indices = section.get("cue_word_indices", [0])
    segments = compute_segments(
        timestamps,
        cue_word_indices,
        audio_duration,
        clean_text=section.get("narration", ""),
    )
    audio_slices = slice_audio(
        audio_path,
        segments,
        output_dir=paths.audio_dir(),
        section_id=section_id,
        # Always re-slice: Phase 1 rewrites <id>.mp3 every run, and an old
        # slice with the same name may come from another plan (#66).
        overwrite=True,
    )
    return segments, audio_slices


def _run_section(
    section: dict,
    idx: int,
    tts_on: bool,
    current_topic_hash: str,
    section_audio: dict | None = None,
    overview: dict | None = None,
) -> SectionOutcome:
    """Run the full pipeline for one section and report what happened (#71).

    Handles TTS, codegen, render, retry, fallback, audio-slice, and per-cue
    muxing. Returns a SectionOutcome: its status, a short reason, and the
    ordered clip paths to assemble (empty when the section is dropped).
    """
    section_id = safe_section_id(section, idx)
    log = logging.LoggerAdapter(logger, {"section": section_id})
    log.info("[manimgen] Section %d: %s", idx, section["title"])

    # --- TTS + segmentation ---
    segments = None
    audio_slices: list[str] = []
    tts_error = ""

    if section_audio is not None:
        # Use precomputed audio from global TTS phase
        segments = section_audio.get("segments") or None
        audio_slices = section_audio.get("audio_slices") or []
        tts_error = section_audio.get("tts_error", "")
        if segments:
            log.info(
                "[manimgen] Using precomputed audio: %d cue segment(s)", len(segments)
            )
    elif tts_on:
        try:
            tts_result = _run_tts_for_section(section, idx)
        except Exception as e:
            tts_error = _error_text(e)
            log.warning("[manimgen] TTS failed for '%s': %s", section["title"], e)
            tts_result = None
        if tts_result:
            segments, audio_slices = _segment_and_slice(section, tts_result, section_id)
            log.info("[manimgen] %d cue segment(s) for this section", len(segments))
            log.info(
                "[manimgen] Audio slices: %s",
                [os.path.basename(p) for p in audio_slices],
            )

    if segments:
        key = _section_key(section, current_topic_hash, [s.duration for s in segments])
        if _all_cues_muxed(section, idx, len(segments), key):
            log.info("[manimgen] All cues already muxed, skipping section")
            clips = [_muxed_path_for(section, idx, i) for i in range(len(segments))]
            status, reason = _cached_outcome(clips[0])
            return SectionOutcome(status, clips, reason)

    # --- Generate ONE scene for the whole section ---
    cue_durations = [seg.duration for seg in segments] if segments else None
    key = _section_key(section, current_topic_hash, cue_durations)

    from manimgen.utils import section_class_name

    class_name = section_class_name(section)
    found_video = _find_rendered_video(class_name)
    cache_is_usable = bool(found_video) and _render_is_fresh(found_video, key)
    # #24: the render-cache / --resume shortcut bypasses EVERY quality gate.
    # A cached section with a multi-second freeze-frame tail would ship
    # unchecked. Re-run the zero-cost timing freeze check against the cached
    # scene source before honoring the cache. A real freeze (post-#23: UNKNOWN
    # cues never block) invalidates the cache and forces full regeneration +
    # retry. No render happens here — this is a static AST check on the .py.
    if cache_is_usable and cue_durations:
        cached_freezes = _cached_scene_blocking_freezes(section, cue_durations)
        if cached_freezes:
            log.warning(
                "[manimgen] Cached render for %s has %d blocking freeze-frame "
                "tail(s) — invalidating cache, regenerating: %s",
                class_name,
                len(cached_freezes),
                "; ".join(cached_freezes),
            )
            cache_is_usable = False

    if cache_is_usable:
        log.info(
            "[manimgen] Render exists and is fresh, skipping codegen: %s", found_video
        )
        render = RenderResult(found_video, *_cached_outcome(found_video))
    else:
        gate = _generate_and_gate(section, cue_durations, overview)
        render = _render_with_retry(section, gate, cue_durations, log)
        # The sidecar records the status too, so a cached fallback card or a
        # render with known defects is still reported as such on --resume.
        if render.ok and os.path.exists(render.path):
            _write_hash_sidecar(render.path, key, render.status, render.reason)

    if not render.ok:
        log.warning("[manimgen] No video for section %d, skipping", idx)
        return SectionOutcome(SectionStatus.DROPPED, [], render.reason)

    # --- Cut + mux per cue ---
    if segments and audio_slices:
        try:
            clips = _cut_and_mux(
                section,
                idx,
                render.path,
                segments,
                audio_slices,
                cue_durations,
                log,
                key,
                render.status,
                render.reason,
            )
        except Exception as e:
            # cut_video_at_cues re-raises the first failed ffmpeg cut (#72).
            log.error("[manimgen] Section %d: cutting into cues failed: %s", idx, e)
            return SectionOutcome(
                SectionStatus.DROPPED, [], f"cutting into cues failed: {_error_text(e)}"
            )
        if not clips:
            return SectionOutcome(
                SectionStatus.DROPPED,
                [],
                "narration could not be muxed onto every cue (see the log)",
            )
        return SectionOutcome(render.status, clips, render.reason)

    if not tts_on:
        # TTS off: the full section video is the clip, silent by choice.
        return SectionOutcome(render.status, [render.path], render.reason)

    # TTS is on but this section has no narration audio: it ships silent.
    if not section.get("narration", "").strip():
        why = "the plan has no narration for this section"
    else:
        why = f"narration failed: {tts_error or 'see the log'}"
    log.warning("[manimgen] Section %d ships with no narration: %s", idx, why)
    if render.status.degraded:
        return SectionOutcome(render.status, [render.path], f"{render.reason}; {why}")
    return SectionOutcome(SectionStatus.SILENT, [render.path], why)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _configure_logging() -> None:
    """Show INFO progress on stderr and keep a DEBUG run log under logs_dir().

    main() is the installed console-script entry point, so logging must be set
    up here rather than under ``__main__`` (#63). If the root logger already
    has handlers (a host app, a test harness, or an earlier call) it is left
    alone so output is never duplicated.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(logging.DEBUG)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(message)s"))
    root.addHandler(console)
    try:
        os.makedirs(paths.logs_dir(), exist_ok=True)
        log_path = os.path.join(
            paths.logs_dir(), time.strftime("run_%Y%m%d_%H%M%S.log")
        )
        run_log = logging.FileHandler(log_path, encoding="utf-8")
    except OSError as e:
        logger.warning("[manimgen] Could not open a run log file (%s)", e)
        return
    run_log.setLevel(logging.DEBUG)
    run_log.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root.addHandler(run_log)


# Exit codes of the `manimgen` command (documented in the README).
EXIT_OK = 0  # every section is ok (or accepted with defects, listed in the summary)
EXIT_FAILED = 1  # refused to run, or no video was produced
EXIT_DEGRADED = 3  # a video was produced, but a section is degraded
EXIT_USAGE_LIMIT = 4  # stopped cleanly on a usage limit; resume after the reset

_MANIFEST_NAME = "run_manifest.json"


def _section_record(
    idx: int, section: dict, outcome: SectionOutcome, seconds: float
) -> dict:
    """One section's line in the run summary and manifest (#71)."""
    return {
        "index": idx,
        "id": safe_section_id(section, idx),
        "title": section.get("title", ""),
        "status": outcome.status.value,
        "reason": outcome.reason,
        "clips": len(outcome.clips),
        "seconds": round(seconds, 1),
    }


def _next_steps(records: list[dict]) -> list[str]:
    """What the user can do about the sections that did not come out clean."""
    statuses = {r["status"] for r in records}
    steps = []
    if statuses & {"dropped", "errored", "silent", "not_run"}:
        steps.append(
            "Fix the cause shown above (details in the run log under "
            f"{paths.logs_dir()}), then run: manimgen --resume. Finished "
            "sections are reused, the others are built again."
        )
    if "fallback" in statuses:
        steps.append(
            "Fallback title cards are reused by --resume. To try one again, "
            f"delete its clips ({paths.muxed_dir()}/<section id>_cue*.mp4) "
            "and run: manimgen --resume."
        )
    return steps


def _write_manifest(manifest: dict, directory: str) -> str | None:
    """Write run_manifest.json atomically (utf-8); return its path or None."""
    path = os.path.join(directory, _MANIFEST_NAME)
    tmp = path + ".tmp"
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("[manimgen] Could not write %s: %s", path, e)
        return None
    return path


def _finish_run(
    title: str,
    content_hash: str,
    records: list[dict],
    output: str | None,
    exit_code: int,
    result: str,
    started: float,
    stop_reason: str = "",
    next_steps: list[str] | None = None,
) -> None:
    """Write the run manifest and print the run summary (#71).

    The manifest goes next to the final video (or into the videos folder when
    there is none), so a script can read what the exit code summarizes.
    """
    steps = _next_steps(records) if next_steps is None else next_steps
    manifest = {
        "title": title,
        "content_hash": content_hash,
        "result": result,
        "exit_code": exit_code,
        "output": output,
        "stop_reason": stop_reason or None,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
        "sections": records,
        "next_steps": steps,
    }
    directory = os.path.dirname(output) if output else paths.videos_dir()
    manifest_path = _write_manifest(manifest, directory or ".")

    counts: dict[str, int] = {}
    for r in records:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    tally = ", ".join(f"{n} {status}" for status, n in counts.items())
    # print(), not logging: the summary must show even when a host mutes logs.
    print(f'\n[manimgen] Run summary for "{title}": {len(records)} sections ({tally})')
    for r in records:
        line = f"  {r['index']:>2}  {r['id']:<12} {r['status']:<22} {r['title']}"
        if r["status"] != "ok" and r["reason"]:
            line += f"\n      {r['reason']}"
        print(line)
    if stop_reason:
        print(f"[manimgen] Stopped: {stop_reason}")
    print(f"[manimgen] Video: {output or 'none (no video was produced)'}")
    if manifest_path:
        print(f"[manimgen] Manifest: {manifest_path}")
    for step in steps:
        print(f"[manimgen] Next: {step}")


def _plan_reset_hint() -> str:
    """' (around <time> local time)' when llm.py has seen the plan's reset."""
    from manimgen import llm

    now = time.time()
    windows = [w for w in getattr(llm, "_plan_windows", {}).values() if w[1] > now]
    if not windows:
        return ""
    _, resets_at = max(windows)  # the fullest window is the one that blocks
    return time.strftime(
        " (around %Y-%m-%d %H:%M local time)", time.localtime(resets_at)
    )


def _stop_for_usage_limit(
    exc: BaseException,
    title: str,
    content_hash: str,
    records: list[dict],
    started: float,
    resumable: bool,
):
    """Stop the run cleanly on a usage limit: manifest, summary, exit 4 (#72).

    Nothing is assembled; sections that finished stay cached for --resume.
    """
    logger.debug("[manimgen] Usage-limit stop", exc_info=exc)
    wait = (
        f"Wait until the limit resets{_plan_reset_hint()} (or change the "
        "setting named above), then "
    )
    if resumable:
        step = wait + "run: manimgen --resume. Finished sections are reused."
    else:
        step = wait + "rerun the same command (no plan was saved yet)."
    _finish_run(
        title,
        content_hash,
        records,
        None,
        EXIT_USAGE_LIMIT,
        "usage_limit",
        started,
        stop_reason=" ".join(str(exc).split()),
        next_steps=[step],
    )
    raise SystemExit(EXIT_USAGE_LIMIT)


def _stop_for_narration(
    lesson_plan: dict, content_hash: str, failed: dict[str, str], started: float
):
    """Stop before any scene is generated when narration failed (#73).

    Only the planning LLM calls were spent at this point. Shipping on would
    spend every codegen and render call on a video with silent sections.
    """
    records = []
    for idx, section in enumerate(lesson_plan["sections"], start=1):
        error = failed.get(safe_section_id(section, idx))
        outcome = (
            SectionOutcome(SectionStatus.ERRORED, [], f"narration failed: {error}")
            if error
            else SectionOutcome(SectionStatus.NOT_RUN)
        )
        records.append(_section_record(idx, section, outcome, 0.0))
    logger.error(
        "[manimgen] Narration failed for %d section(s) after retries; stopping "
        "before scene generation.",
        len(failed),
    )
    _finish_run(
        lesson_plan["title"],
        content_hash,
        records,
        None,
        EXIT_FAILED,
        "narration_failed",
        started,
        stop_reason=(
            f"text to speech failed for {len(failed)} section(s) after retries. "
            "No scene was generated, so only the planning calls were used."
        ),
        next_steps=[
            "Check the network: edge-tts must reach Microsoft's speech "
            "service (behind a proxy, set HTTPS_PROXY or tts.proxy in "
            "config.yaml). Then run: manimgen --resume",
            "To make the video anyway, with those sections silent, run: "
            "manimgen --resume --allow-silent",
        ],
    )
    raise SystemExit(EXIT_FAILED)


def _die(message: str):
    """Print an error to stderr and exit non-zero."""
    print(f"[manimgen] error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _save_plan(lesson_plan: dict) -> None:
    """Write the plan cache atomically so a crash never leaves a torn plan.json."""
    os.makedirs(os.path.dirname(_PLAN_CACHE), exist_ok=True)
    tmp_path = _PLAN_CACHE + ".tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(lesson_plan, f, indent=2)
    os.replace(tmp_path, _PLAN_CACHE)
    logger.info("[manimgen] Plan saved to %s", _PLAN_CACHE)


def _load_cached_plan() -> dict:
    """Load the cached plan for --resume, exiting with a clear error if unusable."""
    if not os.path.exists(_PLAN_CACHE):
        _die(
            f"--resume needs a cached plan but none exists at {_PLAN_CACHE} "
            "(the path is relative to the current directory). "
            "Run without --resume first."
        )
    try:
        with open(_PLAN_CACHE, encoding="utf-8") as f:
            lesson_plan = json.load(f)
    except (OSError, ValueError) as e:
        _die(f"cached plan {_PLAN_CACHE} is corrupt or unreadable ({e}). Delete it.")
    if not isinstance(lesson_plan, dict) or not isinstance(
        lesson_plan.get("sections"), list
    ):
        _die(f"cached plan {_PLAN_CACHE} is corrupt: no sections list. Delete it.")
    return lesson_plan


def main():
    # Windows writes redirected or piped output in the ANSI code page (cp1252),
    # which cannot encode the arrows used in log lines; replace instead of
    # raising so a log line can never abort a run.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(errors="backslashreplace")
            except (OSError, ValueError):
                pass

    parser = argparse.ArgumentParser(description="ManimGen: topic to 3B1B-style video")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("topic", nargs="?", help="Topic string")
    group.add_argument("--pdf", metavar="FILE", help="Path to a PDF of lecture notes")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=(
            f"Resume from the cached plan ({_PLAN_CACHE}). Alone it reuses the "
            "plan as is; with a topic or --pdf the plan must match it or the "
            "run is refused."
        ),
    )
    parser.add_argument(
        "--allow-silent",
        action="store_true",
        help=(
            "If narration (text to speech) still fails after its retries, "
            "make those sections without a voice instead of stopping. They are "
            "flagged in the summary and the run exits 3."
        ),
    )
    args = parser.parse_args()
    if not (args.topic or args.pdf or args.resume):
        parser.error("one of the arguments topic, --pdf or --resume is required")

    _configure_logging()
    started = time.time()

    from manimgen.validator.retry import reset_run_budget

    # The retry LLM budget is per run; nothing else resets it (#71).
    reset_run_budget()

    cfg = _load_config()
    tts_on = _tts_enabled(cfg)
    logger.info(
        "[manimgen] Config: %s | plan and output folders: %s",
        config.config_path(),
        os.path.dirname(paths.plan_cache()),
    )

    # Reset per-run mismatch log so a new run doesn't accumulate stale entries.
    clear_mismatch_log()

    # --- Plan ---
    if args.resume:
        # Refuse rather than replan: replanning spends LLM quota (#64).
        lesson_plan = _load_cached_plan()
        current_topic_hash = lesson_plan.get("_topic_hash", "")
        requested = None
        if args.pdf:
            # The plan stores a hash of the PDF's bytes (#66), so compare that.
            try:
                requested = (args.pdf, _file_hash(args.pdf))
            except OSError as e:
                _die(
                    f"cannot read --pdf {args.pdf} to check it against the cached plan ({e})."
                )
        elif args.topic:
            requested = (args.topic, _topic_hash(parse_input(args.topic)))
        if requested and requested[1] != current_topic_hash:
            _die(
                f"cached plan {_PLAN_CACHE} is for "
                f"'{lesson_plan.get('title', '?')}' and does not match the "
                f"requested input '{requested[0]}'. Rerun without --resume to "
                "plan it, or drop the input to resume the cached plan."
            )
        logger.info("[manimgen] Resuming from cached plan: %s", _PLAN_CACHE)
        if not current_topic_hash:
            logger.warning(
                "[manimgen] Cached plan has no _topic_hash — all renders will be treated as stale"
            )
    else:
        try:
            if args.pdf:
                logger.info("[manimgen] PDF input: %s", args.pdf)
                current_topic_hash = _file_hash(args.pdf)
                lesson_plan = plan_lesson_from_pdf(args.pdf)
            else:
                logger.info("[manimgen] Input: %s", args.topic)
                topic = parse_input(args.topic)
                current_topic_hash = _topic_hash(topic)
                lesson_plan = plan_lesson(topic)
        except Exception as e:
            if not is_usage_stop(e):
                raise
            _stop_for_usage_limit(
                e, args.pdf or args.topic, "", [], started, resumable=False
            )
        lesson_plan["_topic_hash"] = current_topic_hash
        _save_plan(lesson_plan)

    logger.info("[manimgen] Planned %d sections", len(lesson_plan["sections"]))
    logger.info("[manimgen] TTS: %s", "enabled" if tts_on else "disabled")

    content_hash = _content_hash(current_topic_hash, cfg)

    # --- Global audio phase: run all TTS before any codegen ---
    all_section_audio: dict[str, dict] = {}
    if tts_on:
        logger.info(
            "[manimgen] Phase 1: TTS for all %d sections", len(lesson_plan["sections"])
        )
        for idx, section in enumerate(lesson_plan["sections"], start=1):
            section_id = safe_section_id(section, idx)
            try:
                tts_result = _run_tts_for_section(section, idx)
                if tts_result:
                    segments, audio_slices = _segment_and_slice(
                        section, tts_result, section_id
                    )
            except Exception as e:
                logger.warning(
                    "[manimgen] TTS failed for '%s': %s", section.get("title"), e
                )
                all_section_audio[section_id] = {"tts_error": _error_text(e)}
                continue
            if tts_result:
                audio_path, timestamps, audio_duration = tts_result
                all_section_audio[section_id] = {
                    "audio_path": audio_path,
                    "timestamps": timestamps,
                    "audio_duration": audio_duration,
                    "segments": segments,
                    "audio_slices": audio_slices,
                    "cue_durations": [seg.duration for seg in segments],
                }

    tts_failed = {
        sid: audio["tts_error"]
        for sid, audio in all_section_audio.items()
        if "tts_error" in audio
    }
    if tts_failed and not args.allow_silent:
        _stop_for_narration(lesson_plan, content_hash, tts_failed, started)

    overview = _build_overview(lesson_plan, all_section_audio)
    logger.info("[manimgen] Overview: %s", overview["pacing_notes"])

    # --- Phase 2: codegen + render for all sections ---
    title = lesson_plan["title"]
    rendered_videos: list[str] = []
    records: list[dict] = []
    for idx, section in enumerate(lesson_plan["sections"], start=1):
        section_id = safe_section_id(section, idx)
        # When tts_on, always pass section_audio (even {} for failed TTS) so
        # _run_section doesn't re-attempt TTS — global phase already ran it.
        if tts_on:
            section_audio = all_section_audio.get(section_id, {})
        else:
            section_audio = None
        section_started = time.time()
        try:
            outcome = _run_section(
                section,
                idx,
                tts_on,
                content_hash,
                section_audio=section_audio,
                overview=overview,
            )
        except Exception as e:
            outcome = SectionOutcome(SectionStatus.ERRORED, [], _error_text(e))
            if is_usage_stop(e):
                records.append(_section_record(idx, section, outcome, 0.0))
                not_run = SectionOutcome(SectionStatus.NOT_RUN)
                records.extend(
                    _section_record(i, s, not_run, 0.0)
                    for i, s in enumerate(lesson_plan["sections"], start=1)
                    if i > idx
                )
                _stop_for_usage_limit(
                    e, title, content_hash, records, started, resumable=True
                )
            # One failed section must not cost the others (#72): report it,
            # keep the traceback in the run log, and go on.
            logger.error("[manimgen] Section %d (%s) failed: %s", idx, section_id, e)
            logger.debug("[manimgen] Section %d traceback", idx, exc_info=True)
        rendered_videos.extend(outcome.clips)
        records.append(
            _section_record(idx, section, outcome, time.time() - section_started)
        )

    def no_video(message: str):
        _finish_run(
            title, content_hash, records, None, EXIT_FAILED, "no_video", started
        )
        _die(message)

    if not rendered_videos:
        no_video("No video was produced: no section rendered. See the log above.")
    output = assemble_video(rendered_videos, title)
    if not output or not os.path.exists(output):
        no_video(f"No video was produced: expected the final output at {output}.")

    # --- A/V mismatch summary ---
    mismatches = get_mismatch_log()
    if mismatches:
        large = [m for m in mismatches if abs(m.get("diff", 0)) > 1.0]
        logger.info(
            "[manimgen] A/V sync summary: %d cue mismatch(es) (%d large >1s)",
            len(mismatches),
            len(large),
        )
        for m in mismatches:
            diff = m.get("diff", 0)
            level = logger.warning if abs(diff) > 1.0 else logger.info
            level(
                "[manimgen]   %s: video=%.3fs audio=%.3fs diff=%+.3fs",
                os.path.basename(m.get("output_path", "?")),
                m.get("video_dur", 0),
                m.get("audio_dur", 0),
                diff,
            )
    else:
        logger.info("[manimgen] A/V sync: all cues matched within threshold")

    degraded = any(SectionStatus(r["status"]).degraded for r in records)
    exit_code = EXIT_DEGRADED if degraded else EXIT_OK
    _finish_run(
        title,
        content_hash,
        records,
        output,
        exit_code,
        "degraded" if degraded else "ok",
        started,
    )
    # print() so the path shows on stdout even when a host app mutes logging.
    print(f"[manimgen] Done: {output}")
    logger.debug("[manimgen] Done: %s", output)
    if exit_code != EXIT_OK:
        raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
