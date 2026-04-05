import os
import re
from manimgen.llm import chat


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "generator_system.md")) as f:
        return f.read()


def _load_seed_examples(max_examples: int = 3, max_chars_per_example: int = 1200) -> str:
    """
    Token-efficient few-shot context: include a small, bounded subset of verified examples.
    """
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

    user_message = f"""Generate a ManimGL scene for this lesson section.

Section title: {section['title']}
Visual description: {section['visual_description']}
Key objects: {', '.join(section.get('key_objects', []))}
Target duration: {section.get('duration_seconds', 30)} seconds
Class name: {class_name}
"""
    seed_examples = _load_seed_examples()
    if seed_examples:
        user_message += (
            "\nUse these VERIFIED ManimGL examples as style + API references. "
            "They are truncated intentionally for token efficiency:\n\n"
            f"{seed_examples}\n"
        )

    code = chat(system=system, user=user_message)

    # Strip markdown fencing if present
    if code.startswith("```"):
        code = re.sub(r"^```\w*\n?", "", code)
        code = re.sub(r"\n?```$", "", code)

    scenes_dir = "manimgen/output/scenes"
    os.makedirs(scenes_dir, exist_ok=True)
    scene_path = os.path.join(scenes_dir, f"{section['id']}.py")

    with open(scene_path, "w") as f:
        f.write(code)

    return code, class_name, scene_path
