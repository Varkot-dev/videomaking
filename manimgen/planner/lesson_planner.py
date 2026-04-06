import json
import logging
import os
from manimgen.llm import chat

logger = logging.getLogger(__name__)

_MAX_SECTIONS_TOPIC = 8
_MAX_SECTIONS_PDF = 10


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_system.md")) as f:
        return f.read()


def _load_pdf_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_pdf_system.md")) as f:
        return f.read()


def _cap_sections(plan: dict, limit: int) -> dict:
    """Enforce a hard section cap even if the LLM overshoots."""
    sections = plan.get("sections", [])
    if len(sections) > limit:
        logger.warning(
            "[planner] LLM returned %d sections, capping to %d",
            len(sections), limit,
        )
        plan["sections"] = sections[:limit]
    return plan


def _strip_fencing(raw: str) -> str:
    """Remove markdown code fencing if the LLM wrapped the JSON in it."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def _safe_json_loads(raw: str) -> dict:
    """Parse JSON, retrying with escape sanitization if the first attempt fails."""
    import re
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # LLMs sometimes emit invalid escape sequences like \e or \s inside strings.
        # Replace bare backslashes not followed by valid JSON escape chars.
        sanitized = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', raw)
        return json.loads(sanitized)


def plan_lesson(topic: str) -> dict:
    """Call LLM to turn a topic description into a structured lesson plan."""
    system = _load_system_prompt()
    raw = chat(system=system, user=f"Create a lesson plan for: {topic}")
    return _cap_sections(_safe_json_loads(_strip_fencing(raw)), _MAX_SECTIONS_TOPIC)


def plan_lesson_from_pdf(pdf_path: str) -> dict:
    """
    Parse a PDF of lecture notes and produce a deep, structured lesson plan.

    The PDF is parsed into text chunks, the chunks are synthesised into a
    single coherent content summary, and the LLM is asked to produce a
    6–10 section lesson plan faithful to the source material (hard-capped at
    _MAX_SECTIONS_PDF if the model returns too many).

    Returns the same JSON schema as plan_lesson() but with additional
    `source_confidence` fields on each section.
    """
    from manimgen.input.pdf_parser import parse_pdf

    print(f"[planner] Parsing PDF: {pdf_path}")
    parsed = parse_pdf(pdf_path)

    images = parsed.get("images", [])

    if not parsed["raw_text"] and not images:
        raise ValueError(
            f"No content could be extracted from '{pdf_path}'. "
            "The file may be corrupt."
        )

    print(
        f"[planner] Extracted {parsed['extracted_pages']} pages, "
        f"{len(parsed['chunks'])} chunks, "
        f"{len(parsed['raw_text'])} chars, "
        f"{len(images)} image(s)"
    )

    # Build a concise content block for the LLM.
    # We include all chunks but cap total length to avoid context overflow.
    MAX_CHARS = 24_000
    content_parts = []
    total = 0
    for i, chunk in enumerate(parsed["chunks"]):
        entry = f"[Chunk {i + 1}]\n{chunk}"
        if total + len(entry) > MAX_CHARS:
            content_parts.append(
                f"[... {len(parsed['chunks']) - i} additional chunks truncated due to length ...]"
            )
            break
        content_parts.append(entry)
        total += len(entry)

    source_content = "\n\n".join(content_parts)

    if source_content:
        user_message = (
            "Here is the extracted content from the lecture notes PDF. "
            "Create a deep, comprehensive lesson plan based on this material.\n\n"
            f"--- SOURCE MATERIAL ---\n{source_content}\n--- END SOURCE MATERIAL ---"
        )
    else:
        user_message = (
            "Here are images extracted from a lecture notes PDF (no machine-readable text). "
            "Analyse all images and create a deep, comprehensive lesson plan based on what you see."
        )

    # Cap images sent to the LLM to avoid token overflow (max 10)
    MAX_IMAGES = 10
    if len(images) > MAX_IMAGES:
        logger.warning(
            "[planner] %d images found, sending only first %d to LLM", len(images), MAX_IMAGES
        )
        images = images[:MAX_IMAGES]

    system = _load_pdf_system_prompt()
    print(f"[planner] Calling LLM for PDF lesson plan (images: {len(images)})...")
    raw = chat(system=system, user=user_message, images=images if images else None)
    return _cap_sections(_safe_json_loads(_strip_fencing(raw)), _MAX_SECTIONS_PDF)
