import json
import logging
import os
from manimgen.llm import chat
from manimgen.planner.cue_parser import parse_cues

logger = logging.getLogger(__name__)

_MAX_SECTIONS_TOPIC = 6
_MAX_SECTIONS_PDF = 8


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_system.md")) as f:
        return f.read()


def _load_pdf_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_pdf_system.md")) as f:
        return f.read()


def _cap_sections(plan: dict, limit: int) -> dict:
    sections = plan.get("sections", [])
    if len(sections) > limit:
        logger.warning("[planner] LLM returned %d sections, capping to %d", len(sections), limit)
        plan["sections"] = sections[:limit]
    return plan


def _extract_cues(plan: dict) -> dict:
    """Parse [CUE] markers from narration and merge with the cues[] storyboard array.

    After this runs each section has:
      - section["narration"]          clean text (no [CUE] tags), ready for TTS
      - section["cue_word_indices"]   [0, 9, 23, ...] word indices from narration
      - section["cues"]               list of {index, visual} dicts from planner
                                      (synthesised from narration if planner omitted them)
    """
    for section in plan.get("sections", []):
        raw = section.get("narration", "")
        clean, indices = parse_cues(raw)
        section["narration"] = clean
        section["cue_word_indices"] = indices

        # Ensure section["cues"] exists and is indexed correctly
        existing_cues = section.get("cues", [])
        # Fill any missing cue entries with empty visuals
        cues_out = []
        for i in range(len(indices)):
            if i < len(existing_cues):
                entry = dict(existing_cues[i])
                entry["index"] = i
                cues_out.append(entry)
            else:
                cues_out.append({"index": i, "visual": ""})
        section["cues"] = cues_out

        if len(indices) == 1:
            logger.warning(
                "[planner] Section '%s' has no [CUE] markers — single animation segment",
                section.get("id", "?"),
            )
    return plan


from manimgen.utils import strip_fencing as _strip_fencing


def _safe_json_loads(raw: str) -> dict:
    import re
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        return json.loads(sanitized)


def plan_lesson(topic: str) -> dict:
    system = _load_system_prompt()
    raw = chat(system=system, user=f"Create a visual storyboard for: {topic}")
    plan = _cap_sections(_safe_json_loads(_strip_fencing(raw)), _MAX_SECTIONS_TOPIC)
    return _extract_cues(plan)


def plan_lesson_from_pdf(pdf_path: str) -> dict:
    from manimgen.input.pdf_parser import parse_pdf

    print(f"[planner] Parsing PDF: {pdf_path}")
    parsed = parse_pdf(pdf_path)
    images = parsed.get("images", [])

    if not parsed["raw_text"] and not images:
        raise ValueError(f"No content could be extracted from '{pdf_path}'.")

    print(
        f"[planner] Extracted {parsed['extracted_pages']} pages, "
        f"{len(parsed['chunks'])} chunks, "
        f"{len(parsed['raw_text'])} chars, "
        f"{len(images)} image(s)"
    )

    MAX_CHARS = 24_000
    content_parts = []
    total = 0
    for i, chunk in enumerate(parsed["chunks"]):
        entry = f"[Chunk {i + 1}]\n{chunk}"
        if total + len(entry) > MAX_CHARS:
            content_parts.append(f"[... {len(parsed['chunks']) - i} additional chunks truncated ...]")
            break
        content_parts.append(entry)
        total += len(entry)

    source_content = "\n\n".join(content_parts)

    if source_content:
        user_message = (
            "Here is the extracted content from the lecture notes PDF. "
            "Create a visual storyboard based on this material.\n\n"
            f"--- SOURCE MATERIAL ---\n{source_content}\n--- END SOURCE MATERIAL ---"
        )
    else:
        user_message = (
            "Here are images extracted from a lecture notes PDF. "
            "Analyse all images and create a visual storyboard based on what you see."
        )

    MAX_IMAGES = 10
    if len(images) > MAX_IMAGES:
        images = images[:MAX_IMAGES]

    system = _load_pdf_system_prompt()
    print(f"[planner] Calling LLM for PDF lesson plan (images: {len(images)})...")
    raw = chat(system=system, user=user_message, images=images if images else None)
    plan = _cap_sections(_safe_json_loads(_strip_fencing(raw)), _MAX_SECTIONS_PDF)
    return _extract_cues(plan)
