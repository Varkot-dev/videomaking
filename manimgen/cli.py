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
    """Run TTS for a section. Returns (audio_path, timestamps, audio_duration) or None on failure."""
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


def _muxed_path_for(section: dict, idx: int, cue_index: int | None = None) -> str:
    section_id = section.get("id", f"section_{idx:02d}")
    if cue_index is not None:
        return os.path.join(paths.muxed_dir(), f"{section_id}_cue{cue_index:02d}.mp4")
    return os.path.join(paths.muxed_dir(), f"{section_id}.mp4")


def _video_path_for(section: dict, cue_index: int | None = None) -> str:
    """Return the rendered (pre-mux) video path if it exists in the videos dir."""
    section_id = section.get("id", "")
    base = section_id.replace("_", " ").title().replace(" ", "")
    if cue_index is not None:
        class_name = f"{base}Cue{cue_index:02d}Scene"
    else:
        class_name = f"{base}Scene"
    videos_dir = "videos"
    candidate = os.path.join(videos_dir, f"{class_name}.mp4")
    return candidate if os.path.exists(candidate) else ""


def main():
    parser = argparse.ArgumentParser(description="ManimGen: topic to 3B1B-style video")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("topic", nargs="?", help="Topic to explain (e.g. 'binary search')")
    group.add_argument("--pdf", metavar="FILE", help="Path to a PDF of lecture notes")
    parser.add_argument(
        "--resume",
        action="store_true",
        help=f"Resume from saved plan ({_PLAN_CACHE}), skipping already-muxed sections",
    )
    args = parser.parse_args()

    cfg = _load_config()
    tts_on = _tts_enabled(cfg)

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
        print(f"[manimgen] Normalized: {topic}")
        lesson_plan = plan_lesson(topic)
        os.makedirs(os.path.dirname(_PLAN_CACHE), exist_ok=True)
        with open(_PLAN_CACHE, "w") as f:
            json.dump(lesson_plan, f, indent=2)
        print(f"[manimgen] Plan saved to {_PLAN_CACHE}")

    print(f"[manimgen] Planned {len(lesson_plan['sections'])} sections")
    if tts_on:
        print("[manimgen] TTS narration: enabled")
    else:
        print("[manimgen] TTS narration: disabled (set tts.enabled: true in config.yaml to enable)")

    rendered_videos = []
    for idx, section in enumerate(lesson_plan["sections"], start=1):
        print(f"\n[manimgen] Section {idx}: {section['title']}")

        # Step 1: TTS first (when enabled) to get exact per-cue durations
        tts_result = None
        segments = None
        audio_slices = []
        if tts_on:
            tts_result = _run_tts_for_section(section, idx)
            if tts_result:
                from manimgen.planner.segmenter import compute_segments
                from manimgen.renderer.audio_slicer import slice_audio
                audio_path, timestamps, audio_duration = tts_result
                cue_word_indices = section.get("cue_word_indices", [0])
                segments = compute_segments(timestamps, cue_word_indices, audio_duration)
                print(f"[manimgen] {len(segments)} animation segment(s) for this section")
                section_id = section.get("id", f"section_{idx:02d}")
                audio_slices = slice_audio(
                    audio_path, segments,
                    output_dir=paths.audio_dir(),
                    section_id=section_id,
                )
                print(f"[manimgen] Audio slices: {[os.path.basename(p) for p in audio_slices]}")

        # Step 2: Generate + render one sub-scene per cue segment
        # If TTS is off or failed, fall back to single scene with estimated duration
        if segments:
            section_videos = []
            for seg in segments:
                muxed = _muxed_path_for(section, idx, seg.cue_index)
                if os.path.exists(muxed):
                    print(f"[manimgen] Skipping cue {seg.cue_index} (already muxed)")
                    section_videos.append(muxed)
                    continue

                existing = _video_path_for(section, seg.cue_index)
                if existing:
                    video_path = existing
                    success = True
                else:
                    code, class_name, scene_path = generate_scenes(
                        section,
                        cue_index=seg.cue_index,
                        total_cues=seg.total_cues,
                        duration_seconds=seg.duration,
                    )
                    success, video_path = run_scene(scene_path, class_name)
                    if not success:
                        success, video_path = retry_scene(section, code, class_name, scene_path)
                    if not success:
                        print(f"[manimgen] Fallback for cue {seg.cue_index}")
                        video_path = fallback_scene(section)

                if video_path:
                    # Mux video with its matching audio slice
                    audio_slice = audio_slices[seg.cue_index] if audio_slices else None
                    if audio_slice and os.path.exists(audio_slice):
                        from manimgen.renderer.muxer import mux_audio_video
                        try:
                            video_path = mux_audio_video(video_path, audio_slice, muxed)
                            print(f"[manimgen] Muxed cue {seg.cue_index}: {os.path.basename(muxed)}")
                        except Exception as e:
                            logger.warning("[manimgen] Mux failed cue %d: %s — using silent", seg.cue_index, e)
                    section_videos.append(video_path)

            rendered_videos.extend(section_videos)

        else:
            # TTS disabled or failed — single scene per section, duration estimated
            muxed = _muxed_path_for(section, idx)
            if os.path.exists(muxed):
                print(f"[manimgen] Skipping (already muxed): {section['title']}")
                rendered_videos.append(muxed)
                continue

            existing_video = _video_path_for(section)
            if existing_video:
                print(f"[manimgen] Render exists, skipping codegen: {section['title']}")
                video_path = existing_video
            else:
                code, class_name, scene_path = generate_scenes(section)
                success, video_path = run_scene(scene_path, class_name)
                if not success:
                    success, video_path = retry_scene(section, code, class_name, scene_path)
                if not success:
                    print(f"[manimgen] All retries failed for '{section['title']}', using fallback")
                    video_path = fallback_scene(section)

            if video_path:
                rendered_videos.append(video_path)

    output = assemble_video(rendered_videos, lesson_plan["title"])
    print(f"[manimgen] Done: {output}")


if __name__ == "__main__":
    main()
