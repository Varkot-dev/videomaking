import os
import subprocess
import re
from manimgen.llm import chat
from manimgen.validator.env import get_render_env


FALLBACK_TEMPLATE = '''from manimlib import *

class FallbackScene(Scene):
    """Fallback scene: displays section title when code generation fails."""
    def construct(self):
        title = Text({title!r}, font_size=48)
        subtitle = Text("(animation unavailable)", font_size=24, color=GREY)
        subtitle.next_to(title, DOWN, buff=0.4)
        self.play(FadeIn(title))
        self.play(FadeIn(subtitle))
        self.wait(3)
        self.play(FadeOut(title), FadeOut(subtitle))
'''


def fallback_scene(section: dict) -> str | None:
    """Generate and render a constrained fallback scene; if that fails, use title card."""
    scenes_dir = "manimgen/output/scenes"
    os.makedirs(scenes_dir, exist_ok=True)

    scene_path = os.path.join(scenes_dir, f"{section['id']}_fallback.py")
    class_name = f"{section['id'].replace('_', ' ').title().replace(' ', '')}FallbackScene"
    code = _generate_constrained_fallback(section, class_name) or FALLBACK_TEMPLATE.format(title=section["title"])
    code = code.replace("class FallbackScene(Scene):", f"class {class_name}(Scene):")

    with open(scene_path, "w") as f:
        f.write(code)

    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd"],
            capture_output=True,
            text=True,
            timeout=60,
            env=get_render_env(),
        )
        if result.returncode == 0:
            from manimgen.validator.runner import _find_rendered_video
            return _find_rendered_video(class_name)

        # Hard fallback to static title card with a unique class name if constrained code fails.
        code = FALLBACK_TEMPLATE.format(title=section["title"]).replace(
            "class FallbackScene(Scene):", f"class {class_name}(Scene):"
        )
        with open(scene_path, "w") as f:
            f.write(code)
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd"],
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


def _generate_constrained_fallback(section: dict, class_name: str) -> str | None:
    system = (
        "You generate safe ManimGL fallback scenes.\n"
        "Output only Python.\n"
        "Use only: Text, VGroup, FadeIn, FadeOut, Write, ShowCreation, SurroundingRectangle.\n"
        "No Arrow, no Axes, no NumberPlane, no always_redraw, no updaters.\n"
        "Exactly one Scene class with the requested class name.\n"
        "Import only from manimlib.\n"
    )
    user = (
        f"Create a minimal 10-20 second scene for section '{section['title']}'.\n"
        f"Visual description: {section.get('visual_description', '')}\n"
        f"Class name: {class_name}\n"
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
