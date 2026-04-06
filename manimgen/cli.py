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

logger = logging.getLogger(__name__)

_PLAN_CACHE = "manimgen/output/plan.json"


def _load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    try:
        with open(config_path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def _tts_enabled(cfg: dict) -> bool:
    return cfg.get("tts", {}).get("enabled", False)


def _add_narration(section: dict, video_path: str, idx: int) -> str:
    """Generate TTS audio for section and mux it onto the video.

    Returns the muxed video path on success, or the original video path if
    TTS/muxing fails (graceful degradation — never crashes the pipeline).
    """
    narration = section.get("narration", "").strip()
    if not narration:
        return video_path

    from manimgen.renderer.tts import generate_narration
    from manimgen.renderer.muxer import mux_audio_video

    audio_dir = "manimgen/output/audio"
    muxed_dir = "manimgen/output/muxed"
    os.makedirs(audio_dir, exist_ok=True)
    os.makedirs(muxed_dir, exist_ok=True)

    section_id = section.get("id", f"section_{idx:02d}")
    audio_path = os.path.join(audio_dir, f"{section_id}.mp3")
    muxed_path = os.path.join(muxed_dir, f"{section_id}.mp4")

    try:
        print(f"[manimgen] Generating TTS for: {section['title']}")
        generate_narration(narration, audio_path)
    except Exception as e:
        logger.warning(
            "[manimgen] TTS failed for '%s': %s — using silent video",
            section["title"], e,
        )
        return video_path

    try:
        print(f"[manimgen] Muxing audio+video for: {section['title']}")
        mux_audio_video(video_path, audio_path, muxed_path)
        return muxed_path
    except Exception as e:
        logger.warning(
            "[manimgen] Mux failed for '%s': %s — using silent video",
            section["title"], e,
        )
        return video_path


def _muxed_path_for(section: dict, idx: int) -> str:
    section_id = section.get("id", f"section_{idx:02d}")
    return os.path.join("manimgen/output/muxed", f"{section_id}.mp4")


def _video_path_for(section: dict) -> str:
    """Return the rendered (pre-mux) video path if it exists in the videos dir."""
    section_id = section.get("id", "")
    videos_dir = "videos"
    # e.g. videos/Section01Scene.mp4
    class_name = section_id.replace("_", " ").title().replace(" ", "") + "Scene"
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
        muxed = _muxed_path_for(section, idx)

        # Skip sections that are fully done (muxed video exists)
        if os.path.exists(muxed):
            print(f"[manimgen] Skipping (already muxed): {section['title']} → {muxed}")
            rendered_videos.append(muxed)
            continue

        # Check if the raw render exists so we skip codegen + manimgl
        existing_video = _video_path_for(section)
        if existing_video:
            print(f"[manimgen] Render exists, skipping codegen: {section['title']}")
            video_path = existing_video
            success = True
        else:
            print(f"[manimgen] Generating: {section['title']}")
            code, class_name, scene_path = generate_scenes(section)
            success, video_path = run_scene(scene_path, class_name)
            if not success:
                success, video_path = retry_scene(section, code, class_name, scene_path)
            if not success:
                print(f"[manimgen] All retries failed for '{section['title']}', using fallback")
                video_path = fallback_scene(section)

        if video_path:
            if tts_on:
                video_path = _add_narration(section, video_path, idx)
            rendered_videos.append(video_path)

    output = assemble_video(rendered_videos, lesson_plan["title"])
    print(f"[manimgen] Done: {output}")


if __name__ == "__main__":
    main()
