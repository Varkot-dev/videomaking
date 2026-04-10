"""Shared utilities used across multiple manimgen modules."""


import base64
import glob
import os

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


def load_reference_frames() -> list[str]:
    """Load the gold standard 1080p ManimGL aesthetic reference frames as base64."""
    here = os.path.dirname(__file__)
    ref_dir = os.path.join(here, "reference_frames")
    pngs = glob.glob(os.path.join(ref_dir, "*.png"))
    
    frames = []
    for path in sorted(pngs):
        with open(path, "rb") as f:
            frames.append(base64.b64encode(f.read()).decode("utf-8"))
    return frames
