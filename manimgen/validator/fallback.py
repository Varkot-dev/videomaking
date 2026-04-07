import os
import subprocess
from manimgen.validator.env import get_render_env
from manimgen import paths


FALLBACK_TEMPLATE = '''from manimlib import *

class FallbackScene(Scene):
    def construct(self):
        title = Text({title!r}, font_size=42, color=BLUE_B).to_edge(UP, buff=0.8)
        points = VGroup(
            Text({point1!r}, font_size=28, color=WHITE),
            Text({point2!r}, font_size=28, color=WHITE),
            Text({point3!r}, font_size=28, color=WHITE),
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
    """Generate and render a deterministic fallback scene (no LLM call)."""
    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)

    hold_seconds = _estimate_hold(section)
    scene_path = os.path.join(scenes_dir, f"{section['id']}_fallback.py")
    class_name = f"{section['id'].replace('_', ' ').title().replace(' ', '')}FallbackScene"
    points = _fallback_points(section)
    code = FALLBACK_TEMPLATE.format(
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
            timeout=180,
            env=get_render_env(),
        )
        if result.returncode == 0:
            from manimgen.validator.runner import _find_rendered_video
            return _find_rendered_video(class_name)

        # deterministic fallback has no second strategy; fail fast
    except subprocess.TimeoutExpired:
        pass

    return None


def _fallback_points(section: dict) -> list[str]:
    cues = section.get("cues", [])
    visuals = []
    for cue in cues:
        text = str(cue.get("visual", "")).strip()
        if text:
            # Keep lines compact so fallback text does not overflow.
            visuals.append(text[:72] + ("..." if len(text) > 72 else ""))
    if visuals:
        points = visuals[:3]
        while len(points) < 3:
            points.append("Additional visual cue")
        return points

    return [
        "Core idea overview",
        "Key visual relationship",
        "Main takeaway",
    ]
