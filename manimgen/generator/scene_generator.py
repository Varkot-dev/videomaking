import math
import os
import re
from manimgen.llm import chat

_WORDS_PER_MINUTE = 130


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "generator_system.md")) as f:
        return f.read()


def _load_seed_examples(max_examples: int = 3, max_chars_per_example: int = 1200) -> str:
    here = os.path.dirname(__file__)
    project_root = os.path.dirname(os.path.dirname(here))
    examples_dir = os.path.join(project_root, "examples")
    if not os.path.isdir(examples_dir):
        return ""

    names = sorted([n for n in os.listdir(examples_dir) if n.endswith(".py")])[:max_examples]
    blocks: list[str] = []
    for name in names:
        path = os.path.join(examples_dir, name)
        with open(path) as f:
            content = f.read().strip()
        snippet = content[:max_chars_per_example]
        if len(content) > max_chars_per_example:
            snippet += "\n# ... truncated for token efficiency"
        blocks.append(f"### {name}\n```python\n{snippet}\n```")
    return "\n\n".join(blocks)


def _estimate_narration_duration(narration: str) -> int:
    """Estimate how many seconds the narration will take when spoken via TTS."""
    words = len(narration.split())
    return max(10, math.ceil(words / _WORDS_PER_MINUTE * 60))


def _class_name_from_section(section: dict) -> str:
    title = section["id"].replace("_", " ").title().replace(" ", "")
    return f"{title}Scene"


def generate_scenes(section: dict) -> tuple[str, str, str]:
    """
    Generate a ManimGL scene file for a single lesson section.
    Returns (code, class_name, scene_path).
    """
    system = _load_system_prompt()
    class_name = _class_name_from_section(section)

    narration = section.get("narration", "")
    if narration:
        target_seconds = _estimate_narration_duration(narration)
    else:
        target_seconds = section.get("duration_seconds", 30)

    user_message = f"""Generate a ManimGL scene for this lesson section.

Section title: {section['title']}
Visual description: {section['visual_description']}
Key objects: {', '.join(section.get('key_objects', []))}
Class name: {class_name}

CRITICAL — Duration target: {target_seconds} seconds.
The narration for this section is {len(narration.split())} words long and will take ~{target_seconds}s when spoken.
Your animation MUST last approximately {target_seconds} seconds total (sum of all self.play run_times + self.wait durations).
Distribute self.wait() calls across the scene to fill the target duration. Do NOT make the scene shorter or longer.

LAYOUT RULES (MANDATORY — violations will be rejected):
1) NEVER place text ON TOP of a graph/shape/diagram; labels go outside using next_to(..., buff>=0.3).
2) If text must overlay visuals, use BackgroundRectangle(label, fill_opacity=0.85, buff=0.1).
3) Title goes at top: to_edge(UP, buff=0.8). Main content: center().shift(DOWN*0.5) when title is visible.
4) Axes MUST be explicitly sized: Axes(...).set_width(10).center() (shift DOWN*0.5 when title is present).
5) Keep objects in safe zone x=[-6,6], y=[-3.5,3.5].
6) Fade out previous content before introducing new visual groups.
7) Keep visible objects manageable (target <= 12).
8) Use clear size hierarchy: title~56, equation~42-48, labels~28-32, ticks~24.
9) End cleanly: self.play(*[FadeOut(m) for m in self.mobjects]); self.wait(0.5)
"""
    seed_examples = _load_seed_examples()
    if seed_examples:
        user_message += (
            "\nUse these VERIFIED ManimGL examples as style + API references. "
            "They are truncated intentionally for token efficiency:\n\n"
            f"{seed_examples}\n"
        )

    code = chat(system=system, user=user_message)

    if code.startswith("```"):
        code = re.sub(r"^```\w*\n?", "", code)
        code = re.sub(r"\n?```$", "", code)

    scenes_dir = "manimgen/output/scenes"
    os.makedirs(scenes_dir, exist_ok=True)
    scene_path = os.path.join(scenes_dir, f"{section['id']}.py")

    with open(scene_path, "w") as f:
        f.write(code)

    return code, class_name, scene_path
