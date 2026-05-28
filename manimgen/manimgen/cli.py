import argparse
import hashlib
import json
import logging
import os
from collections.abc import Callable

import yaml

from manimgen import paths
from manimgen.generator.scene_generator import generate_scenes
from manimgen.input.parser import parse_input
from manimgen.planner.lesson_planner import plan_lesson, plan_lesson_from_pdf
from manimgen.renderer.assembler import assemble_video
from manimgen.renderer.muxer import clear_mismatch_log, get_mismatch_log
from manimgen.types import CueMuxResult, GateResult, MuxStatus
from manimgen.utils import safe_section_id
from manimgen.validator.fallback import fallback_scene
from manimgen.validator.retry import retry_scene
from manimgen.validator.runner import _find_rendered_video, run_scene

logger = logging.getLogger(__name__)

_PLAN_CACHE = paths.plan_cache()


def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        # A malformed/unreadable config.yaml silently disables TTS and can
        # route output to default dirs — make the failure visible.
        logger.warning(
            "[manimgen] Failed to load config %s (%s) — using empty config "
            "(TTS may be disabled, defaults applied)",
            config_path,
            e,
        )
        return {}


def _tts_enabled(cfg: dict) -> bool:
    return cfg.get("tts", {}).get("enabled", False)


def _run_tts_for_section(section: dict, idx: int) -> tuple[str, list, float] | None:
    """Run TTS for a section. Returns (audio_path, timestamps, audio_duration) or None."""
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

    try:
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
    except Exception as e:
        logger.warning("[manimgen] TTS failed for '%s': %s", section["title"], e)
        return None


def _muxed_path_for(section: dict, idx: int, cue_index: int) -> str:
    section_id = safe_section_id(section, idx)
    return os.path.join(paths.muxed_dir(), f"{section_id}_cue{cue_index:02d}.mp4")


def _all_cues_muxed(section: dict, idx: int, n_cues: int) -> bool:
    return all(os.path.exists(_muxed_path_for(section, idx, i)) for i in range(n_cues))


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

    if os.path.exists(muxed):
        log.info("[manimgen] Skipping cue %d (already muxed)", cue_index)
        return CueMuxResult(cue_index, MuxStatus.SUCCESS, muxed)

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


def _sidecar_hash_path(video_path: str) -> str:
    """Sidecar file that stores the topic hash next to a rendered video."""
    return video_path + ".hash"


def _render_is_fresh(video_path: str, topic_hash: str) -> bool:
    """Return True only if the video exists AND was rendered for this topic."""
    if not os.path.exists(video_path):
        return False
    sidecar = _sidecar_hash_path(video_path)
    if not os.path.exists(sidecar):
        # Legacy render with no sidecar — treat as stale to be safe
        logger.warning(
            "[manimgen] No .hash sidecar for %s — treating as stale render",
            os.path.basename(video_path),
        )
        return False
    with open(sidecar) as f:
        stored = f.read().strip()
    if stored != topic_hash:
        logger.warning(
            "[manimgen] Stale render detected: %s was built for topic hash %s, current is %s — re-rendering",
            os.path.basename(video_path),
            stored,
            topic_hash,
        )
        return False
    return True


def _write_hash_sidecar(video_path: str, topic_hash: str) -> None:
    with open(_sidecar_hash_path(video_path), "w") as f:
        f.write(topic_hash)


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
    scene_path = os.path.join(paths.scenes_dir(), f"{section_id}.py")
    if not section_id or not os.path.exists(scene_path):
        return []
    try:
        with open(scene_path) as f:
            code = f.read()
    except OSError:
        return []

    return _code_blocking_freezes(code, cue_durations)


# ---------------------------------------------------------------------------
# Per-section pipeline seams (#31)
#
# _run_section was a 160+ line function doing six jobs; the render-cache
# fast-path bypassed the timing freeze gate that the codegen path runs. It is
# now decomposed into three pure-ish seams with explicit signatures:
#
#   _generate_and_gate  — codegen + zero-cost timing gate  → GateResult
#   _render_with_retry  — first render + validate + retry + fallback → (ok, path)
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
    apply an LLM fix with the timing warnings in context).
    """
    code, class_name, scene_path = generate_scenes(
        section, cue_durations=cue_durations, overview=overview
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
) -> tuple[bool, str | None]:
    """Render a gated scene, forcing the retry/fallback path on any failure.

    The first render is skipped entirely when ``gate.timing_blocked`` is set.
    A successful first render is still re-checked for hard visual failures
    (validate_render) and blocking freeze-frame tails — either forces the retry
    path. If retries fail, the styled fallback scene is used. Returns
    (success, video_path); video_path is None only if the fallback also failed.
    """
    if gate.timing_blocked:
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

    if not success:
        success, video_path = retry_scene(
            section,
            gate.code,
            gate.class_name,
            gate.scene_path,
            cue_durations=cue_durations,
        )

    if not success:
        log.info("[manimgen] All retries failed, using fallback")
        video_path = fallback_scene(section)
        success = bool(video_path)

    return success, video_path


def _cut_and_mux(
    section: dict,
    idx: int,
    video_path: str,
    segments: list,
    audio_slices: list[str],
    cue_durations: list[float],
    log: logging.LoggerAdapter | logging.Logger,
) -> list[str]:
    """Cut a rendered section into per-cue clips and mux narration onto each.

    Returns the ordered list of muxed clip paths. If ANY cue fails to mux with
    narration, the whole section is dropped (returns []) and logged loudly — a
    silent clip must never reach the assembler (#28). A FAILED cue's silent
    video is deliberately never appended to the produced list.
    """
    from manimgen.renderer.cutter import (
        cue_start_times_from_durations,
        cut_video_at_cues,
    )
    from manimgen.renderer.muxer import mux_audio_video

    section_id = safe_section_id(section, idx)
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


def _run_section(
    section: dict,
    idx: int,
    tts_on: bool,
    current_topic_hash: str,
    section_audio: dict | None = None,
    overview: dict | None = None,
) -> list[str]:
    """Run the full pipeline for one section and return a list of video paths to assemble.

    Handles TTS, codegen, render, retry, fallback, audio-slice, and per-cue muxing.
    Returns the ordered list of clip paths produced (may be empty if section is skipped).
    """
    section_id = safe_section_id(section, idx)
    log = logging.LoggerAdapter(logger, {"section": section_id})
    log.info("[manimgen] Section %d: %s", idx, section["title"])

    # --- TTS + segmentation ---
    segments = None
    audio_slices: list[str] = []

    if section_audio is not None:
        # Use precomputed audio from global TTS phase
        segments = section_audio.get("segments") or None
        audio_slices = section_audio.get("audio_slices") or []
        if segments:
            log.info(
                "[manimgen] Using precomputed audio: %d cue segment(s)", len(segments)
            )
            if _all_cues_muxed(section, idx, len(segments)):
                log.info("[manimgen] All cues already muxed, skipping section")
                return [_muxed_path_for(section, idx, i) for i in range(len(segments))]
    elif tts_on:
        tts_result = _run_tts_for_section(section, idx)
        if tts_result:
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
            log.info("[manimgen] %d cue segment(s) for this section", len(segments))

            if _all_cues_muxed(section, idx, len(segments)):
                log.info("[manimgen] All cues already muxed, skipping section")
                return [_muxed_path_for(section, idx, i) for i in range(len(segments))]

            audio_slices = slice_audio(
                audio_path,
                segments,
                output_dir=paths.audio_dir(),
                section_id=section_id,
            )
            log.info(
                "[manimgen] Audio slices: %s",
                [os.path.basename(p) for p in audio_slices],
            )

    # --- Generate ONE scene for the whole section ---
    cue_durations = [seg.duration for seg in segments] if segments else None

    from manimgen.utils import section_class_name

    class_name = section_class_name(section)
    found_video = _find_rendered_video(class_name)
    cache_is_usable = bool(found_video) and _render_is_fresh(
        found_video, current_topic_hash
    )
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
        video_path = found_video
        success = True
    else:
        gate = _generate_and_gate(section, cue_durations, overview)
        success, video_path = _render_with_retry(section, gate, cue_durations, log)
        # Write hash sidecar after any successful render (including fallback)
        if success and video_path and os.path.exists(video_path):
            _write_hash_sidecar(video_path, current_topic_hash)

    if not video_path:
        log.warning("[manimgen] No video for section %d, skipping", idx)
        return []

    # --- Cut + mux per cue ---
    if segments and audio_slices and success:
        return _cut_and_mux(
            section, idx, video_path, segments, audio_slices, cue_durations, log
        )

    # TTS off — use the full section video directly
    return [video_path]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="ManimGen: topic to 3B1B-style video")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("topic", nargs="?", help="Topic string")
    group.add_argument("--pdf", metavar="FILE", help="Path to a PDF of lecture notes")
    parser.add_argument(
        "--resume", action="store_true", help=f"Resume from cached plan ({_PLAN_CACHE})"
    )
    args = parser.parse_args()

    cfg = _load_config()
    tts_on = _tts_enabled(cfg)

    # Reset per-run mismatch log so a new run doesn't accumulate stale entries.
    clear_mismatch_log()

    # --- Plan ---
    if args.resume and os.path.exists(_PLAN_CACHE):
        logger.info("[manimgen] Resuming from cached plan: %s", _PLAN_CACHE)
        with open(_PLAN_CACHE) as f:
            lesson_plan = json.load(f)
        # Recover topic hash from cached plan (stored during original run)
        current_topic_hash = lesson_plan.get("_topic_hash", "")
        if not current_topic_hash:
            logger.warning(
                "[manimgen] Cached plan has no _topic_hash — all renders will be treated as stale"
            )
    elif args.pdf:
        logger.info("[manimgen] PDF input: %s", args.pdf)
        current_topic_hash = _topic_hash(os.path.abspath(args.pdf))
        lesson_plan = plan_lesson_from_pdf(args.pdf)
        lesson_plan["_topic_hash"] = current_topic_hash
        os.makedirs(os.path.dirname(_PLAN_CACHE), exist_ok=True)
        with open(_PLAN_CACHE, "w") as f:
            json.dump(lesson_plan, f, indent=2)
        logger.info("[manimgen] Plan saved to %s", _PLAN_CACHE)
    else:
        logger.info("[manimgen] Input: %s", args.topic)
        topic = parse_input(args.topic)
        current_topic_hash = _topic_hash(topic)
        lesson_plan = plan_lesson(topic)
        lesson_plan["_topic_hash"] = current_topic_hash
        os.makedirs(os.path.dirname(_PLAN_CACHE), exist_ok=True)
        with open(_PLAN_CACHE, "w") as f:
            json.dump(lesson_plan, f, indent=2)
        logger.info("[manimgen] Plan saved to %s", _PLAN_CACHE)

    logger.info("[manimgen] Planned %d sections", len(lesson_plan["sections"]))
    logger.info("[manimgen] TTS: %s", "enabled" if tts_on else "disabled")

    # --- Global audio phase: run all TTS before any codegen ---
    all_section_audio: dict[str, dict] = {}
    if tts_on:
        logger.info(
            "[manimgen] Phase 1: TTS for all %d sections", len(lesson_plan["sections"])
        )
        for idx, section in enumerate(lesson_plan["sections"], start=1):
            section_id = safe_section_id(section, idx)
            tts_result = _run_tts_for_section(section, idx)
            if tts_result:
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
                )
                all_section_audio[section_id] = {
                    "audio_path": audio_path,
                    "timestamps": timestamps,
                    "audio_duration": audio_duration,
                    "segments": segments,
                    "audio_slices": audio_slices,
                    "cue_durations": [seg.duration for seg in segments],
                }

    overview = _build_overview(lesson_plan, all_section_audio)
    logger.info("[manimgen] Overview: %s", overview["pacing_notes"])

    # --- Phase 2: codegen + render for all sections ---
    rendered_videos: list[str] = []
    for idx, section in enumerate(lesson_plan["sections"], start=1):
        section_id = safe_section_id(section, idx)
        # When tts_on, always pass section_audio (even {} for failed TTS) so
        # _run_section doesn't re-attempt TTS — global phase already ran it.
        if tts_on:
            section_audio = all_section_audio.get(section_id, {})
        else:
            section_audio = None
        rendered_videos.extend(
            _run_section(
                section,
                idx,
                tts_on,
                current_topic_hash,
                section_audio=section_audio,
                overview=overview,
            )
        )

    output = assemble_video(rendered_videos, lesson_plan["title"])

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

    logger.info("[manimgen] Done: %s", output)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    main()
