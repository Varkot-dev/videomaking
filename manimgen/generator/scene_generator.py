"""
Scene Director — replaces the old spec/template engine.

Takes a section storyboard (from the planner) plus exact cue durations (from TTS segmenter)
and asks the LLM to write one complete ManimGL Scene class per section.

The generated scene contains self.wait() pauses at each cue boundary so the full section
renders as a single continuous mp4. The assembler/muxer then cuts it at cue timestamps.
"""
import math
import os
import re
from manimgen.llm import chat
from manimgen import paths
from manimgen.validator.codeguard import precheck_and_autofix

_WORDS_PER_MINUTE = 130
_MAX_EXAMPLE_CHARS = 1000  # chars per example shown to the Director


def _load_director_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "director_system.md")) as f:
        return f.read()


def _load_examples(max_examples: int = 5) -> str:
    """Load verified example scenes as few-shot references for the Director."""
    here = os.path.dirname(__file__)
    examples_dir = os.path.normpath(os.path.join(here, "..", "..", "examples"))
    if not os.path.isdir(examples_dir):
        return ""
    names = sorted(f for f in os.listdir(examples_dir) if f.endswith(".py"))[:max_examples]
    blocks = []
    for name in names:
        with open(os.path.join(examples_dir, name)) as f:
            content = f.read().strip()
        snippet = content[:_MAX_EXAMPLE_CHARS]
        if len(content) > _MAX_EXAMPLE_CHARS:
            snippet += "\n# ... (truncated)"
        blocks.append(f"### {name}\n```python\n{snippet}\n```")
    return "\n\n".join(blocks)


def _estimate_duration(narration: str) -> float:
    words = len(narration.split())
    return max(10.0, math.ceil(words / _WORDS_PER_MINUTE * 60))


def _class_name(section: dict) -> str:
    return section["id"].replace("_", " ").title().replace(" ", "") + "Scene"


def _strip_fencing(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _build_user_message(section: dict, cue_durations: list[float]) -> str:
    """Build the Director's user prompt from the storyboard + exact cue durations."""
    cues = section.get("cues", [])
    total = sum(cue_durations)

    lines = [
        f"Section title: {section['title']}",
        f"Total duration: {total:.2f}s",
        f"Number of cues: {len(cue_durations)}",
        "",
        "## Cue breakdown",
    ]
    for i, dur in enumerate(cue_durations):
        visual = ""
        if i < len(cues):
            visual = cues[i].get("visual", "")
        lines.append(f"CUE {i} ({dur:.2f}s): {visual}")

    lines += [
        "",
        f"## Class name",
        f"Use exactly: `{_class_name(section)}`",
        "",
        "## Task",
        "Write one complete ManimGL Scene class that animates all cues in sequence.",
        "Use self.wait() between cues to hit each cue's exact duration.",
        "Output ONLY the Python code. No explanation.",
    ]
    return "\n".join(lines)


def generate_scenes(
    section: dict,
    cue_durations: list[float] | None = None,
    duration_seconds: float | None = None,
    # Legacy params kept for backward compat — ignored in new architecture
    cue_index: int | None = None,
    total_cues: int | None = None,
) -> tuple[str, str, str]:
    """Generate one ManimGL scene file for a full section.

    Args:
        section:          The lesson plan section dict with storyboard cues.
        cue_durations:    List of exact durations per cue (from TTS segmenter).
                          If None, falls back to estimating from narration.
        duration_seconds: Total section duration (used when cue_durations is None).

    Returns:
        (code, class_name, scene_path)
    """
    class_name = _class_name(section)

    # Resolve cue durations
    if cue_durations:
        durations = cue_durations
    elif duration_seconds is not None:
        # Single cue covering the whole section
        durations = [float(duration_seconds)]
    else:
        narration = section.get("narration", "")
        total = _estimate_duration(narration) if narration else float(section.get("duration_seconds", 30))
        n_cues = max(1, len(section.get("cue_word_indices", [0])))
        durations = [total / n_cues] * n_cues

    system = _load_director_prompt()
    examples = _load_examples()
    user_message = _build_user_message(section, durations)

    if examples:
        user_message += (
            "\n\n## Verified ManimGL examples (style + API reference)\n\n"
            f"{examples}"
        )

    raw = chat(system=system, user=user_message)
    code = _strip_fencing(raw)

    # Ensure it starts with the correct import
    if not code.startswith("from manimlib"):
        code = "from manimlib import *\n\n\n" + code

    # Run codeguard auto-fixes before saving
    code = precheck_and_autofix(code)

    # Save scene file
    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)
    scene_path = os.path.join(scenes_dir, f"{section['id']}.py")
    with open(scene_path, "w") as f:
        f.write(code)

    return code, class_name, scene_path
