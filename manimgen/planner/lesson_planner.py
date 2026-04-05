import json
import os
from manimgen.llm import chat


def _load_system_prompt() -> str:
    here = os.path.dirname(__file__)
    with open(os.path.join(here, "prompts", "planner_system.md")) as f:
        return f.read()


def plan_lesson(topic: str) -> dict:
    """Call LLM to turn a topic description into a structured lesson plan."""
    system = _load_system_prompt()
    raw = chat(system=system, user=f"Create a lesson plan for: {topic}")

    # Strip markdown fencing if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    return json.loads(raw)
