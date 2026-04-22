import logging
import os
import re
import subprocess
from manimgen.validator.env import get_render_env
from manimgen import paths

logger = logging.getLogger(__name__)

# Styled title card fallback — on-brand, always renderable (I8 · reach floor).
# Design: section number + title + subtitle rule + horizontal divider.
# font_size values are from the canonical type scale (I4).
FALLBACK_TEMPLATE = '''from manimlib import *

class FallbackScene(Scene):
    def construct(self):
        num = Text({section_num!r}, font_size=48, color=GREY_A).to_edge(UP, buff=1.2)
        title = Text({title!r}, font_size=48, color=WHITE).center()
        subtitle = Text({subtitle!r}, font_size=28, color=GREY_A)
        subtitle.next_to(title, DOWN, buff=0.4)
        rule = Line(LEFT * 4, RIGHT * 4, color=GREY_B, stroke_width=1.5)
        rule.next_to(title, DOWN, buff=1.0)
        self.play(FadeIn(num), run_time=0.5)
        self.play(Write(title), run_time=1.0)
        self.play(FadeIn(subtitle), ShowCreation(rule), run_time=0.8)
        self.wait({hold_seconds})
        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
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
    section_num = _section_num(section)
    subtitle = _fallback_subtitle(section)
    title = section["title"]
    if len(title) > 52:
        title = title[:49] + "..."
    code = FALLBACK_TEMPLATE.format(
        section_num=section_num,
        title=title,
        subtitle=subtitle,
        hold_seconds=hold_seconds,
    )
    code = code.replace("class FallbackScene(Scene):", f"class {class_name}(Scene):")

    with open(scene_path, "w") as f:
        f.write(code)

    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", paths.render_quality_flag(), "--fps", str(paths.render_fps()), "-c", "#1C1C1C"],
            capture_output=True,
            text=True,
            timeout=180,
            env=get_render_env(),
        )
        if result.returncode == 0:
            from manimgen.validator.runner import _find_rendered_video
            return _find_rendered_video(class_name)

        # deterministic fallback has no second strategy; fail fast
        logger.warning(
            "[fallback] manimgl exited %d for %s", result.returncode, class_name
        )
    except subprocess.TimeoutExpired:
        logger.warning("[fallback] %s timed out after 180s", class_name)

    return None


def _section_num(section: dict) -> str:
    import re
    sid = section.get("id", "")
    m = re.search(r"(\d+)", sid)
    if m:
        return m.group(1).zfill(2)
    return "00"


def _fallback_subtitle(section: dict) -> str:
    # Use the first sentence of narration — human-readable, no storyboard junk
    narration = section.get("narration", "")
    if narration:
        sentence = re.split(r"[.!?]", narration)[0].strip()
        if sentence:
            return sentence[:60] + ("..." if len(sentence) > 60 else "")
        words = narration.split()
        return " ".join(words[:8]) + ("..." if len(words) > 8 else "")
    return "Visual overview"
