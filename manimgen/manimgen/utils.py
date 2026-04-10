"""Shared utilities used across multiple manimgen modules."""


def strip_fencing(raw: str) -> str:
    """Strip markdown code fences from an LLM response."""
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw.strip()


def section_class_name(section: dict) -> str:
    """Derive the ManimGL Scene class name from a section dict."""
    return section["id"].replace("_", " ").title().replace(" ", "") + "Scene"
