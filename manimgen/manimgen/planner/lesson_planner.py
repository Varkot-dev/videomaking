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


def _load_researcher_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "researcher_system.md")) as f:
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

        # Ensure section["cues"] exists and is indexed correctly.
        # cues[] must have len(indices) entries — one per segment (including the
        # opening segment at index 0, before the first [CUE]).
        existing_cues = section.get("cues", [])
        n_segments = len(indices)
        if existing_cues and len(existing_cues) != n_segments:
            logger.warning(
                "[planner] Section '%s' has %d cues[] but %d segments (%d [CUE] markers). "
                "Planner likely omitted the opening cue (index 0). Synthesising fallbacks for missing entries.",
                section.get("id", "?"),
                len(existing_cues),
                n_segments,
                n_segments - 1,
            )

        title = section.get("title", "")
        cues_out = []
        for i in range(n_segments):
            if i < len(existing_cues):
                entry = dict(existing_cues[i])
                entry["index"] = i
                cues_out.append(entry)
            else:
                # Synthesise a minimal fallback so the Director never receives visual: "".
                fallback = f"Section '{title}': animate segment {i + 1} of {n_segments}."
                logger.warning(
                    "[planner] Section '%s' cue %d has no visual description — using fallback: %r",
                    section.get("id", "?"), i, fallback,
                )
                cues_out.append({"index": i, "visual": fallback})
        section["cues"] = cues_out

        if len(indices) == 1:
            logger.warning(
                "[planner] Section '%s' has no [CUE] markers — single animation segment",
                section.get("id", "?"),
            )
    return plan


from manimgen.utils import strip_fencing as _strip_fencing


def _safe_json_loads(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return json.loads(_escape_bad_backslashes(raw))


def _escape_bad_backslashes(s: str) -> str:
    """Escape backslashes that are not valid JSON escape sequences.

    JSON allows: \\\\ \\" \\/ \\b \\f \\n \\r \\t \\uXXXX
    LaTeX in LLM output often contains valid \\n, \\t but also
    \\theta, \\nabla, \\alpha etc. which are invalid JSON escapes.
    We walk the string and double-escape any backslash not followed by a
    valid JSON escape character.
    """
    valid_escapes = set('"\\\/bfnrtu')
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == '\\':
            if i + 1 < len(s) and s[i + 1] in valid_escapes:
                out.append(ch)          # keep valid escape as-is
            else:
                out.append('\\\\')      # double-escape bare backslash
            i += 1
        else:
            out.append(ch)
        i += 1
    return ''.join(out)


def research_topic(topic: str) -> dict:
    """Call LLM with researcher prompt to build a structured knowledge brief.

    Returns a dict with keys: topic, prerequisites, core_concepts, key_formulas,
    worked_example, failure_modes, real_world_connections, section_suggestions.
    """
    system = _load_researcher_system_prompt()
    raw = chat(system=system, user=f"Research this topic for an educational video: {topic}")
    try:
        brief = _safe_json_loads(_strip_fencing(raw))
        logger.info("[planner] Research brief: %d core concepts, %d formulas",
                    len(brief.get("core_concepts", [])),
                    len(brief.get("key_formulas", [])))
        return brief
    except Exception as e:
        logger.warning("[planner] Failed to parse research brief: %s — continuing without research", e)
        return {}


def _format_research_brief(brief: dict) -> str:
    """Format the research brief as source material for the planner prompt."""
    if not brief:
        return ""

    lines = ["--- RESEARCH BRIEF ---"]

    prerequisites = brief.get("prerequisites", [])
    if prerequisites:
        lines.append(f"Prerequisites: {', '.join(prerequisites)}")

    historical_context = brief.get("historical_context", "")
    if historical_context:
        lines.append(f"\nHistorical Context:\n  {historical_context}")

    t_vs_i = brief.get("textbook_vs_intuition", {})
    if t_vs_i:
        lines.append("\nTextbook vs Intuition:")
        if "textbook" in t_vs_i:
            lines.append(f"  Textbook: {t_vs_i['textbook']}")
        if "intuition" in t_vs_i:
            lines.append(f"  Intuition: {t_vs_i['intuition']}")

    perspectives = brief.get("multiple_perspectives", {})
    if perspectives:
        lines.append("\nMultiple Perspectives:")
        for k, v in perspectives.items():
            lines.append(f"  • {k.title()}: {v}")

    core_concepts = brief.get("core_concepts", [])
    if core_concepts:
        lines.append("\nCore concepts:")
        for c in core_concepts:
            name = c.get("name", "")
            if not name:
                continue
            lines.append(f"  • {name}: {c.get('explanation', '')}")
            if c.get("common_misconception"):
                lines.append(f"    Misconception: {c['common_misconception']}")
            if c.get("visual_opportunity"):
                lines.append(f"    Visual: {c['visual_opportunity']}")

    key_formulas = brief.get("key_formulas", [])
    if key_formulas:
        lines.append("\nKey formulas:")
        for f in key_formulas:
            name = f.get("name", "")
            if not name:
                continue
            lines.append(f"  • {name}: {f.get('formula', '')}")
            lines.append(f"    {f.get('explanation', '')}")

    worked_example = brief.get("worked_example", {})
    if worked_example:
        lines.append(f"\nWorked example: {worked_example.get('description', '')}")
        for step in worked_example.get("steps", []):
            lines.append(f"  {step}")

    failure_modes = brief.get("failure_modes", [])
    if failure_modes:
        lines.append("\nFailure modes / edge cases:")
        for fm in failure_modes:
            name = fm.get("name", "")
            if not name:
                continue
            lines.append(f"  • {name}: {fm.get('description', '')}")
            if fm.get("visual_opportunity"):
                lines.append(f"    Visual: {fm['visual_opportunity']}")

    real_world = brief.get("real_world_connections", [])
    if real_world:
        lines.append("\nReal-world connections: " + "; ".join(real_world))

    section_suggestions = brief.get("section_suggestions", [])
    if section_suggestions:
        lines.append("\nSuggested section flow:")
        for i, s in enumerate(section_suggestions, 1):
            lines.append(f"  {i}. {s}")

    lines.append("--- END RESEARCH BRIEF ---")
    return "\n".join(lines)


def plan_lesson(topic: str) -> dict:
    print(f"[planner] Researching topic: {topic}")
    brief = research_topic(topic)
    research_material = _format_research_brief(brief)

    system = _load_system_prompt()
    if research_material:
        user_message = (
            f"Create a visual storyboard for: {topic}\n\n"
            f"{research_material}"
        )
    else:
        user_message = f"Create a visual storyboard for: {topic}"

    raw = chat(system=system, user=user_message)
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
