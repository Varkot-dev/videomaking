import json
import os
from manimgen.llm import chat


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_system.md")) as f:
        return f.read()


def _load_pdf_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_pdf_system.md")) as f:
        return f.read()


def _strip_fencing(raw: str) -> str:
    """Remove markdown code fencing if the LLM wrapped the JSON in it."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def plan_lesson(topic: str) -> dict:
    """Call LLM to turn a topic description into a structured lesson plan."""
    system = _load_system_prompt()
    raw = chat(system=system, user=f"Create a lesson plan for: {topic}")
    return json.loads(_strip_fencing(raw))


def plan_lesson_from_pdf(pdf_path: str) -> dict:
    """
    Parse a PDF of lecture notes and produce a deep, structured lesson plan.

    The PDF is parsed into text chunks, the chunks are synthesised into a
    single coherent content summary, and the LLM is asked to produce a
    15-20 section lesson plan faithful to the source material.

    Returns the same JSON schema as plan_lesson() but with additional
    `source_confidence` fields on each section.
    """
    from manimgen.input.pdf_parser import parse_pdf

    print(f"[planner] Parsing PDF: {pdf_path}")
    parsed = parse_pdf(pdf_path)

    if not parsed["raw_text"]:
        raise ValueError(
            f"No text could be extracted from '{pdf_path}'. "
            "The file may be image-only or corrupt."
        )

    print(
        f"[planner] Extracted {parsed['extracted_pages']} pages, "
        f"{len(parsed['chunks'])} chunks, "
        f"{len(parsed['raw_text'])} chars"
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

    user_message = (
        "Here is the extracted content from the lecture notes PDF. "
        "Create a deep, comprehensive lesson plan based on this material.\n\n"
        f"--- SOURCE MATERIAL ---\n{source_content}\n--- END SOURCE MATERIAL ---"
    )

    system = _load_pdf_system_prompt()
    print("[planner] Calling LLM for PDF lesson plan...")
    raw = chat(system=system, user=user_message)
    return json.loads(_strip_fencing(raw))
