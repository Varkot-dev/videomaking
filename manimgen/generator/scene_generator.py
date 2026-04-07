import json
import math
import os
import re
from manimgen.llm import chat
from manimgen import paths
from manimgen.templates.spec_schema import SpecValidationError
from manimgen.templates.dispatch import render_spec
from manimgen.validator.retry import retry_spec, MAX_SPEC_RETRIES

_WORDS_PER_MINUTE = 130


def _load_spec_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "spec_system.md")) as f:
        return f.read()


def _estimate_narration_duration(narration: str) -> int:
    """Fallback duration estimate from word count (used when no TTS timestamps available)."""
    words = len(narration.split())
    return max(10, math.ceil(words / _WORDS_PER_MINUTE * 60))


def _class_name_from_section(section: dict, cue_index: int | None = None) -> str:
    title = section["id"].replace("_", " ").title().replace(" ", "")
    if cue_index is not None:
        return f"{title}Cue{cue_index:02d}Scene"
    return f"{title}Scene"


def _strip_fencing(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _safe_json_loads(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        return json.loads(sanitized)


def _generate_spec(
    section: dict,
    cue_index: int | None,
    total_cues: int | None,
    duration_seconds: float,
) -> dict:
    system = _load_spec_system_prompt()
    user_message = f"""Section title: {section['title']}
Visual description: {section['visual_description']}
Key objects: {', '.join(section.get('key_objects', []))}
Duration: {duration_seconds:.2f} seconds

Pick the most appropriate template. Output only valid JSON.
"""
    if cue_index is not None and total_cues and total_cues > 1:
        user_message += f"\nCUE: This is segment {cue_index + 1} of {total_cues}. Animate only the relevant part."

    raw = chat(system=system, user=user_message)
    return _safe_json_loads(_strip_fencing(raw))


def _validate_spec(spec: dict) -> None:
    from manimgen.templates.spec_schema import validate
    from manimgen.validator.codeguard import validate_spec_safety
    errors = validate(spec)
    errors += validate_spec_safety(spec)
    if errors:
        raise SpecValidationError("\n".join(errors))


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
    class_name = _class_name_from_section(section, cue_index)

    # Duration: prefer exact value from TTS, fall back to estimate
    if duration_seconds is not None:
        target_seconds = duration_seconds
    else:
        narration = section.get("narration", "")
        target_seconds = _estimate_narration_duration(narration) if narration else section.get("duration_seconds", 30)

    # Step 1: LLM produces a visual spec JSON; retry on validation failure
    spec = _generate_spec(section, cue_index, total_cues, target_seconds)
    for attempt in range(MAX_SPEC_RETRIES + 1):
        try:
            _validate_spec(spec)
            break
        except SpecValidationError as exc:
            if attempt == MAX_SPEC_RETRIES:
                raise
            print(f"[scene] Spec validation failed (attempt {attempt + 1}/{MAX_SPEC_RETRIES + 1}), retrying: {exc}")
            spec = retry_spec(
                section, cue_index, total_cues, target_seconds,
                errors=str(exc).splitlines(),
            )

    # Save spec for debugging
    specs_dir = os.path.join(os.path.dirname(paths.scenes_dir()), "specs")
    os.makedirs(specs_dir, exist_ok=True)
    if cue_index is not None:
        spec_filename = f"{section['id']}_cue{cue_index:02d}.json"
    else:
        spec_filename = f"{section['id']}.json"
    spec_path = os.path.join(specs_dir, spec_filename)
    with open(spec_path, "w") as f:
        json.dump(spec, f, indent=2)

    # Step 3: Template engine renders the .py file

    scenes_dir = paths.scenes_dir()
    os.makedirs(scenes_dir, exist_ok=True)

    if cue_index is not None:
        scene_filename = f"{section['id']}_cue{cue_index:02d}.py"
    else:
        scene_filename = f"{section['id']}.py"
    scene_path = os.path.join(scenes_dir, scene_filename)

    code = render_spec(spec, class_name, scene_path)

    return code, class_name, scene_path
