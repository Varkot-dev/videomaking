import argparse
import sys
from manimgen.input.parser import parse_input
from manimgen.planner.lesson_planner import plan_lesson
from manimgen.generator.scene_generator import generate_scenes
from manimgen.validator.runner import run_scene
from manimgen.validator.retry import retry_scene
from manimgen.validator.fallback import fallback_scene
from manimgen.renderer.assembler import assemble_video


def main():
    parser = argparse.ArgumentParser(description="ManimGen: topic to 3B1B-style video")
    parser.add_argument("topic", help="Topic to explain (e.g. 'binary search')")
    args = parser.parse_args()

    print(f"[manimgen] Input: {args.topic}")

    topic = parse_input(args.topic)
    print(f"[manimgen] Normalized: {topic}")

    lesson_plan = plan_lesson(topic)
    print(f"[manimgen] Planned {len(lesson_plan['sections'])} sections")

    rendered_videos = []
    for section in lesson_plan["sections"]:
        print(f"[manimgen] Generating: {section['title']}")
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
