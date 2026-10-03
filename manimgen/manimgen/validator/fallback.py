import logging
import math
import os
import re

from manimgen import paths
from manimgen.utils import safe_section_id
from manimgen.validator.render_command import run_manimgl
from manimgen.validator.scene_ast_gate import inspect_scene_code

logger = logging.getLogger(__name__)

# Styled title card fallback — on-brand, always renderable (I8 · reach floor).
# Design: section number + title + subtitle rule + horizontal divider.
# font_size values are from the canonical type scale (I4).
FALLBACK_TEMPLATE = """from manimlib import *

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
"""


def _estimate_hold(section: dict) -> int:
    """Match fallback hold duration to narration length so muxing doesn't distort."""
    narration = section.get("narration", "")
    if narration:
        words = len(narration.split())
        return max(5, math.ceil(words / 130 * 60))
    # Formatted into scene source unquoted, so it must be a plain number: a
    # string from a hand-edited or resumed plan.json would be injected as code.
    try:
        return max(1, min(600, math.ceil(float(section.get("duration_seconds", 10)))))
    except (TypeError, ValueError):
        return 10


def fallback_scene(section: dict) -> str | None:
    """Generate and render a deterministic fallback scene (no LLM call)."""
    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)

    hold_seconds = _estimate_hold(section)
    # Defense in depth: sanitize the id again at this filesystem sink. The
    # resume path can load a plan.json that bypassed parse-time sanitization,
    # and this sink writes a .py file manimgl then executes.
    safe_id = safe_section_id(section)
    scene_path = os.path.join(scenes_dir, f"{safe_id}_fallback.py")
    class_name = f"{safe_id.replace('_', ' ').title().replace(' ', '')}FallbackScene"
    section_num = _section_num(section)
    subtitle = _fallback_subtitle(section)
    title = section["title"]
    if len(title) > 52:
        title = title[:49] + "..."
    code = _fallback_code(class_name, section_num, title, subtitle, hold_seconds)
    gate = inspect_scene_code(code)
    if not gate.ok:
        # The text came from the plan (e.g. a URL in the title). Show neutral
        # text rather than lose the section; run_manimgl gates the file anyway.
        logger.warning(
            "[fallback] title text rejected by the scene safety gate (%s); "
            "using generic text",
            "; ".join(gate.findings),
        )
        code = _fallback_code(
            class_name,
            section_num,
            f"Section {section_num}",
            "Visual overview",
            hold_seconds,
        )

    with open(scene_path, "w", encoding="utf-8") as f:
        f.write(code)

    result = run_manimgl(
        scene_path, class_name, timeout=paths.render_timeout("fallback")
    )
    if result.ok:
        return result.video_path

    # deterministic fallback has no second strategy; fail fast
    logger.warning(
        "[fallback] render failed for %s: %s",
        class_name,
        (result.stderr or "").strip()[-300:],
    )
    return None


def _fallback_code(
    class_name: str, section_num: str, title: str, subtitle: str, hold_seconds: int
) -> str:
    code = FALLBACK_TEMPLATE.format(
        section_num=section_num,
        title=title,
        subtitle=subtitle,
        hold_seconds=hold_seconds,
    )
    return code.replace("class FallbackScene(Scene):", f"class {class_name}(Scene):")


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
