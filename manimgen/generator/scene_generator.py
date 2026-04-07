import math
import os
import re
from manimgen.llm import chat
from manimgen import paths

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
    """Fallback duration estimate from word count (used when no TTS timestamps available)."""
    words = len(narration.split())
    return max(10, math.ceil(words / _WORDS_PER_MINUTE * 60))


def _class_name_from_section(section: dict, cue_index: int | None = None) -> str:
    title = section["id"].replace("_", " ").title().replace(" ", "")
    if cue_index is not None:
        return f"{title}Cue{cue_index:02d}Scene"
    return f"{title}Scene"


def generate_scenes(
    section: dict,
    cue_index: int | None = None,
    total_cues: int | None = None,
    duration_seconds: float | None = None,
) -> tuple[str, str, str]:
    """Generate a ManimGL scene file for one animation segment.

    Args:
        section:          The lesson plan section dict (title, visual_description, etc.)
        cue_index:        Which cue segment this is (0-based). None = whole section.
        total_cues:       Total number of cue segments in this section. None = 1.
        duration_seconds: Exact duration from TTS timestamps. If None, falls back
                          to word-count estimate (used when TTS is disabled).

    Returns:
        (code, class_name, scene_path)
    """
    system = _load_system_prompt()
    class_name = _class_name_from_section(section, cue_index)

    # Duration: prefer exact value from TTS, fall back to estimate
    if duration_seconds is not None:
        target_seconds = duration_seconds
        duration_source = "exact (from TTS word timestamps)"
    else:
        narration = section.get("narration", "")
        target_seconds = _estimate_narration_duration(narration) if narration else section.get("duration_seconds", 30)
        duration_source = "estimated (TTS not yet run)"

    # Cue context for the prompt
    if cue_index is not None and total_cues is not None and total_cues > 1:
        cue_context = (
            f"\nCUE SEGMENT: This is segment {cue_index + 1} of {total_cues} "
            f"for this section.\n"
            f"Animate the {'first' if cue_index == 0 else 'next'} visual idea from the "
            f"description below. Each segment covers a distinct part of the explanation.\n"
            f"Do NOT animate the entire visual_description — only the part relevant to "
            f"segment {cue_index + 1}.\n"
        )
    else:
        cue_context = ""

    user_message = f"""Generate a ManimGL scene for this lesson section.

Section title: {section['title']}
Visual description: {section['visual_description']}
Key objects: {', '.join(section.get('key_objects', []))}
Class name: {class_name}
{cue_context}
CRITICAL — Duration target: {target_seconds:.2f} seconds ({duration_source}).
Your animation MUST last approximately {target_seconds:.2f} seconds total
(sum of all self.play run_times + self.wait durations).
Distribute self.wait() calls to fill the target duration exactly.
Do NOT make the scene shorter or longer.

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

    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)

    if cue_index is not None:
        scene_filename = f"{section['id']}_cue{cue_index:02d}.py"
    else:
        scene_filename = f"{section['id']}.py"
    scene_path = os.path.join(scenes_dir, scene_filename)

    with open(scene_path, "w") as f:
        f.write(code)

    return code, class_name, scene_path
