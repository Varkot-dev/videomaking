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
from manimgen.utils import strip_fencing, section_class_name
from manimgen.validator.codeguard import precheck_and_autofix

_WORDS_PER_MINUTE = 130
_MAX_EXAMPLES = 6


def _load_director_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "director_system.md")) as f:
        return f.read()


def _index_examples() -> dict[str, list[str]]:
    """
    Build technique → [filepath, ...] index by reading the `techniques:` tag
    from each example's docstring. No hardcoded mappings.

    The tag format is:
        techniques: technique_a, technique_b
    as the first line inside the class docstring.
    """
    here = os.path.dirname(__file__)
    examples_dir = os.path.normpath(os.path.join(here, "..", "..", "examples"))
    index: dict[str, list[str]] = {}
    if not os.path.isdir(examples_dir):
        return index

    tag_re = re.compile(r'techniques:\s*(.+)', re.IGNORECASE)
    for fname in sorted(os.listdir(examples_dir)):
        if not fname.endswith(".py"):
            continue
        path = os.path.join(examples_dir, fname)
        with open(path) as f:
            head = f.read(512)  # tag always near the top
        m = tag_re.search(head)
        if not m:
            continue
        for technique in [t.strip() for t in m.group(1).split(",")]:
            index.setdefault(technique, []).append(path)

    return index


def _select_examples(section: dict, index: dict[str, list[str]]) -> list[str]:
    """Return up to _MAX_EXAMPLES full example file paths relevant to this section."""
    # Always include these two as baseline context
    here = os.path.dirname(__file__)
    examples_dir = os.path.normpath(os.path.join(here, "..", "..", "examples"))
    baseline = [
        os.path.join(examples_dir, "graph_scene.py"),
        os.path.join(examples_dir, "stagger_build_scene.py"),
    ]
    selected: list[str] = [p for p in baseline if os.path.isfile(p)]

    # Add technique-specific examples from cue visual fields
    for cue in section.get("cues", []):
        visual = cue.get("visual", "").lower()
        for technique, paths_list in index.items():
            if technique in visual:
                for p in paths_list:
                    if p not in selected:
                        selected.append(p)

    return selected[:_MAX_EXAMPLES]


def _load_examples_text(section: dict) -> str:
    index = _index_examples()
    selected = _select_examples(section, index)
    if not selected:
        return ""
    blocks = []
    for path in selected:
        with open(path) as f:
            content = f.read().strip()
        blocks.append(f"### {os.path.basename(path)}\n```python\n{content}\n```")
    return "\n\n".join(blocks)


def _estimate_duration(narration: str) -> float:
    words = len(narration.split())
    return max(10.0, math.ceil(words / _WORDS_PER_MINUTE * 60))


def _build_user_message(section: dict, cue_durations: list[float]) -> str:
    """Build the Director's user prompt from the storyboard + exact cue durations."""
    cues = section.get("cues", [])
    total = sum(cue_durations)
    class_name = section_class_name(section)

    lines = [
        "## Scene to generate",
        f"Class name (use exactly): `{class_name}`",
        f"Section title: {section['title']}",
        f"Total duration: {total:.2f}s",
        f"Number of cues: {len(cue_durations)}",
        "",
        "## Cue breakdown",
        "(Animate each cue, then self.wait() to fill its exact duration.)",
        "",
    ]
    for i, dur in enumerate(cue_durations):
        visual = cues[i].get("visual", "") if i < len(cues) else ""
        lines.append(f"### CUE {i} — {dur:.2f}s")
        lines.append(visual)
        lines.append("")

    lines += [
        "## Hard constraints",
        "1. `from manimlib import *` — first line.",
        "2. One class only. No helper functions outside construct().",
        "3. Timing: per cue, sum(run_time values) + self.wait() = cue duration exactly.",
        "4. Title zone (y > 2.6): section title Text only.",
        "5. Axes: `width=` and `height=` only — never `x_length=` or `y_length=`.",
        "6. `Tex()` has no `font_size=` — use `.scale()`. `Text()` accepts `font_size=`.",
        "7. Use `ShowCreation()`, `Tex()`, `self.frame` — never ManimCommunity equivalents.",
        "8. VISUAL CONTINUITY: Never show a black screen. Only FadeOut an element when something new replaces it. "
           "If a cue has no new animation, add a relevant annotation, label, or highlight instead of waiting on empty screen. "
           "End the scene with a gentle FadeOut only on the very last cue.",
        "9. Use at least 2 different techniques from the Cinematic Technique Reference.",
        "10. Store all data values in plain Python variables/lists — never read state back from mobject attributes.",
        "",
        "Output ONLY Python. No explanation, no markdown fencing.",
    ]
    return "\n".join(lines)


def generate_scenes(
    section: dict,
    cue_durations: list[float] | None = None,
    duration_seconds: float | None = None,
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
    class_name = section_class_name(section)

    if cue_durations:
        durations = cue_durations
    elif duration_seconds is not None:
        durations = [float(duration_seconds)]
    else:
        narration = section.get("narration", "")
        total = _estimate_duration(narration) if narration else float(section.get("duration_seconds", 30))
        n_cues = max(1, len(section.get("cue_word_indices", [0])))
        durations = [total / n_cues] * n_cues

    system = _load_director_prompt()
    examples = _load_examples_text(section)
    user_message = _build_user_message(section, durations)

    if examples:
        user_message += (
            "\n\n## Verified ManimGL reference scenes — copy patterns exactly, do not invent new APIs\n\n"
            + examples
        )

    raw = chat(system=system, user=user_message)
    code = strip_fencing(raw)

    if not code.startswith("from manimlib"):
        code = "from manimlib import *\n\n\n" + code

    # If any cue visual requests a 3D technique, promote Scene → ThreeDScene
    _3d_techniques = {"3d_surface", "camera_rotation"}
    cue_visuals = " ".join(
        c.get("visual", "").lower() for c in section.get("cues", [])
    )
    if any(t in cue_visuals for t in _3d_techniques):
        code = re.sub(r'\bclass\s+(\w+)\(Scene\):', r'class \1(ThreeDScene):', code)

    code = precheck_and_autofix(code)

    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)
    scene_path = os.path.join(scenes_dir, f"{section['id']}.py")
    with open(scene_path, "w") as f:
        f.write(code)

    return code, class_name, scene_path
