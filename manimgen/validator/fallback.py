import os
import subprocess
import re
from manimgen.llm import chat
from manimgen.validator.env import get_render_env
from manimgen import paths


FALLBACK_TEMPLATE = '''from manimlib import *

class FallbackScene(Scene):
    def construct(self):
        title = Text({title!r}, font_size=52, color=BLUE_B).to_edge(UP, buff=0.8)
        points = VGroup(
            Text({point1!r}, font_size=30, color=WHITE),
            Text({point2!r}, font_size=30, color=WHITE),
            Text({point3!r}, font_size=30, color=WHITE),
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.35).center().shift(DOWN * 0.3)
        bullets = VGroup(*[
            Dot(p.get_left() + LEFT * 0.3, radius=0.06, fill_color=YELLOW)
            for p in points
        ])
        content = VGroup(points, bullets)
        self.play(Write(title), run_time=1.2)
        self.play(LaggedStartMap(FadeIn, points, lag_ratio=0.25), run_time=1.8)
        self.play(FadeIn(bullets), run_time=0.5)
        self.wait({hold_seconds})
        self.play(FadeOut(content), FadeOut(title), run_time=0.8)
'''


def _estimate_hold(section: dict) -> int:
    """Match fallback hold duration to narration length so muxing doesn't distort."""
    narration = section.get("narration", "")
    if narration:
        import math
        words = len(narration.split())
        return max(5, math.ceil(words / 130 * 60))
    return section.get("duration_seconds", 10)


def fallback_scene(section: dict) -> str | None:
    """Generate and render a constrained fallback scene; if that fails, use title card."""
    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)

    hold_seconds = _estimate_hold(section)
    scene_path = os.path.join(scenes_dir, f"{section['id']}_fallback.py")
    class_name = f"{section['id'].replace('_', ' ').title().replace(' ', '')}FallbackScene"
    points = _fallback_points(section)
    code = _generate_constrained_fallback(section, class_name, hold_seconds) or FALLBACK_TEMPLATE.format(
        title=section["title"],
        hold_seconds=hold_seconds,
        point1=points[0],
        point2=points[1],
        point3=points[2],
    )
    code = code.replace("class FallbackScene(Scene):", f"class {class_name}(Scene):")

    with open(scene_path, "w") as f:
        f.write(code)

    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd", "-c", "#1C1C1C"],
            capture_output=True,
            text=True,
            timeout=60,
            env=get_render_env(),
        )
        if result.returncode == 0:
            from manimgen.validator.runner import _find_rendered_video
            return _find_rendered_video(class_name)

        points = _fallback_points(section)
        code = FALLBACK_TEMPLATE.format(
            title=section["title"],
            hold_seconds=hold_seconds,
            point1=points[0],
            point2=points[1],
            point3=points[2],
        ).replace("class FallbackScene(Scene):", f"class {class_name}(Scene):")
        with open(scene_path, "w") as f:
            f.write(code)
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd", "-c", "#1C1C1C"],
            capture_output=True,
            text=True,
            timeout=60,
            env=get_render_env(),
        )
        if result.returncode == 0:
            from manimgen.validator.runner import _find_rendered_video
            return _find_rendered_video(class_name)
    except subprocess.TimeoutExpired:
        pass

    return None


def _load_fallback_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "fallback_system.md")) as f:
        return f.read()


def _generate_constrained_fallback(section: dict, class_name: str, hold_seconds: int = 10) -> str | None:
    system = _load_fallback_system_prompt()
    user = (
        f"Create a minimal scene lasting ~{hold_seconds} seconds for section '{section['title']}'.\n"
        f"Visual description: {section.get('visual_description', '')}\n"
        f"Class name: {class_name}\n"
        f"Use self.wait() calls totaling ~{hold_seconds - 5}s (leave ~5s for animations).\n"
    )
    try:
        code = chat(system=system, user=user)
    except Exception:
        return None
    if not code:
        return None
    if code.startswith("```"):
        code = re.sub(r"^```\w*\n?", "", code)
        code = re.sub(r"\n?```$", "", code)
    return code


def _fallback_points(section: dict) -> list[str]:
    keys = [str(k).replace("_", " ") for k in section.get("key_objects", []) if str(k).strip()]
    if not keys:
        return [
            "Core idea overview",
            "Key visual relationship",
            "Main takeaway",
        ]
    points = [f"Key object: {k}" for k in keys[:3]]
    while len(points) < 3:
        points.append("Additional supporting detail")
    return points
