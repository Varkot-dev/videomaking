import argparse
import json
import logging
import os
import sys

import yaml

from manimgen.input.parser import parse_input
from manimgen.planner.lesson_planner import plan_lesson, plan_lesson_from_pdf
from manimgen.generator.scene_generator import generate_scenes
from manimgen.validator.runner import run_scene
from manimgen.validator.retry import retry_scene
from manimgen.validator.fallback import fallback_scene
from manimgen.renderer.assembler import assemble_video
from manimgen import paths

logger = logging.getLogger(__name__)

_PLAN_CACHE = paths.plan_cache()


def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _tts_enabled(cfg: dict) -> bool:
    return cfg.get("tts", {}).get("enabled", False)


def _run_tts_for_section(section: dict, idx: int) -> tuple[str, list, float] | None:
    """Run TTS for a section. Returns (audio_path, timestamps, audio_duration) or None."""
    from manimgen.renderer.tts import generate_narration, save_timestamps, get_audio_duration

    narration = section.get("narration", "").strip()
    if not narration:
        return None

    section_id = section.get("id", f"section_{idx:02d}")
    audio_dir = paths.audio_dir()
    os.makedirs(audio_dir, exist_ok=True)
    audio_path = os.path.join(audio_dir, f"{section_id}.mp3")

    try:
        print(f"[manimgen] TTS: {section['title']}")
        _, timestamps = generate_narration(narration, audio_path)
        ts_path = audio_path.replace(".mp3", "_timestamps.json")
        save_timestamps(timestamps, ts_path)
        audio_duration = get_audio_duration(audio_path)
        print(f"[manimgen] {len(timestamps)} word timestamps, {audio_duration:.1f}s audio")
        return audio_path, timestamps, audio_duration
    except Exception as e:
        logger.warning("[manimgen] TTS failed for '%s': %s", section["title"], e)
        return None


def _muxed_path_for(section: dict, idx: int, cue_index: int) -> str:
    section_id = section.get("id", f"section_{idx:02d}")
    return os.path.join(paths.muxed_dir(), f"{section_id}_cue{cue_index:02d}.mp4")


def _all_cues_muxed(section: dict, idx: int, n_cues: int) -> bool:
    return all(os.path.exists(_muxed_path_for(section, idx, i)) for i in range(n_cues))


def _rendered_section_path(section: dict) -> str:
    """Path to the full (pre-cut) rendered section video from ManimGL."""
    section_id = section.get("id", "")
    class_name = section_id.replace("_", " ").title().replace(" ", "") + "Scene"
    return os.path.join("videos", f"{class_name}.mp4")


def main():
    parser = argparse.ArgumentParser(description="ManimGen: topic to 3B1B-style video")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("topic", nargs="?", help="Topic string")
    group.add_argument("--pdf", metavar="FILE", help="Path to a PDF of lecture notes")
    parser.add_argument("--resume", action="store_true",
                        help=f"Resume from cached plan ({_PLAN_CACHE})")
    args = parser.parse_args()

    cfg = _load_config()
    tts_on = _tts_enabled(cfg)

    # --- Plan ---
    if args.resume and os.path.exists(_PLAN_CACHE):
        print(f"[manimgen] Resuming from cached plan: {_PLAN_CACHE}")
        with open(_PLAN_CACHE) as f:
            lesson_plan = json.load(f)
    elif args.pdf:
        print(f"[manimgen] PDF input: {args.pdf}")
        lesson_plan = plan_lesson_from_pdf(args.pdf)
        os.makedirs(os.path.dirname(_PLAN_CACHE), exist_ok=True)
        with open(_PLAN_CACHE, "w") as f:
            json.dump(lesson_plan, f, indent=2)
        print(f"[manimgen] Plan saved to {_PLAN_CACHE}")
    else:
        print(f"[manimgen] Input: {args.topic}")
        topic = parse_input(args.topic)
        lesson_plan = plan_lesson(topic)
        os.makedirs(os.path.dirname(_PLAN_CACHE), exist_ok=True)
        with open(_PLAN_CACHE, "w") as f:
            json.dump(lesson_plan, f, indent=2)
        print(f"[manimgen] Plan saved to {_PLAN_CACHE}")

    print(f"[manimgen] Planned {len(lesson_plan['sections'])} sections")
    print(f"[manimgen] TTS: {'enabled' if tts_on else 'disabled'}")

    rendered_videos: list[str] = []

    for idx, section in enumerate(lesson_plan["sections"], start=1):
        print(f"\n[manimgen] Section {idx}: {section['title']}")

        # --- TTS + segmentation ---
        tts_result = None
        segments = None
        audio_slices: list[str] = []

        if tts_on:
            tts_result = _run_tts_for_section(section, idx)
            if tts_result:
                from manimgen.planner.segmenter import compute_segments
                from manimgen.renderer.audio_slicer import slice_audio

                audio_path, timestamps, audio_duration = tts_result
                cue_word_indices = section.get("cue_word_indices", [0])
                segments = compute_segments(timestamps, cue_word_indices, audio_duration)
                n_cues = len(segments)
                print(f"[manimgen] {n_cues} cue segment(s) for this section")

                section_id = section.get("id", f"section_{idx:02d}")
                audio_slices = slice_audio(
                    audio_path, segments,
                    output_dir=paths.audio_dir(),
                    section_id=section_id,
                )
                print(f"[manimgen] Audio slices: {[os.path.basename(p) for p in audio_slices]}")

        # --- Check if all cues already muxed ---
        if segments and _all_cues_muxed(section, idx, len(segments)):
            print(f"[manimgen] All cues already muxed, skipping section")
            for i in range(len(segments)):
                rendered_videos.append(_muxed_path_for(section, idx, i))
            continue

        # --- Generate ONE scene for the whole section ---
        cue_durations = [seg.duration for seg in segments] if segments else None

        existing_video = _rendered_section_path(section)
        if os.path.exists(existing_video):
            print(f"[manimgen] Render exists, skipping codegen: {existing_video}")
            video_path = existing_video
            success = True
        else:
            code, class_name, scene_path = generate_scenes(
                section,
                cue_durations=cue_durations,
            )
            success, video_path = run_scene(scene_path, class_name)
            if not success:
                success, video_path = retry_scene(section, code, class_name, scene_path)
            if not success:
                print(f"[manimgen] All retries failed, using fallback")
                video_path = fallback_scene(section)
                success = bool(video_path)

        if not video_path:
            print(f"[manimgen] No video for section {idx}, skipping")
            continue

        # --- Cut + mux per cue ---
        if segments and audio_slices and success:
            from manimgen.renderer.cutter import cut_video_at_cues, cue_start_times_from_durations
            from manimgen.renderer.muxer import mux_audio_video

            cue_durations_list = [seg.duration for seg in segments]
            cue_starts = cue_start_times_from_durations(cue_durations_list)

            try:
                cue_video_clips = cut_video_at_cues(
                    video_path,
                    cue_starts,
                    cue_durations_list,
                    output_dir=paths.muxed_dir(),
                    section_id=section.get("id", f"section_{idx:02d}"),
                )
            except Exception as e:
                logger.warning("[manimgen] Cue cutting failed: %s — using full video per cue", e)
                cue_video_clips = [video_path] * len(segments)

            for i, (cue_clip, audio_slice) in enumerate(zip(cue_video_clips, audio_slices)):
                muxed = _muxed_path_for(section, idx, i)
                if os.path.exists(muxed):
                    print(f"[manimgen] Skipping cue {i} (already muxed)")
                    rendered_videos.append(muxed)
                    continue
                if os.path.exists(audio_slice):
                    try:
                        mux_audio_video(cue_clip, audio_slice, muxed)
                        print(f"[manimgen] Muxed cue {i}: {os.path.basename(muxed)}")
                        rendered_videos.append(muxed)
                    except Exception as e:
                        logger.warning("[manimgen] Mux failed cue %d: %s", i, e)
                        rendered_videos.append(cue_clip)
                else:
                    rendered_videos.append(cue_clip)
        else:
            # TTS off — use the full section video directly
            rendered_videos.append(video_path)

    output = assemble_video(rendered_videos, lesson_plan["title"])
    print(f"\n[manimgen] Done: {output}")


if __name__ == "__main__":
    main()
