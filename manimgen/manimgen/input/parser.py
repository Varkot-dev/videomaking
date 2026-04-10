import re


def parse_input(raw: str) -> str:
    """Normalize raw user input into a clean topic description."""
    text = raw.strip()
    text = re.sub(r"\s+", " ", text)
    return text
