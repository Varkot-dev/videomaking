"""PreToolUse hook: block edits inside manim/, the read-only ManimGL submodule.

Reads the hook JSON from stdin. Exit code 2 blocks the tool call. Works with
both / and \\ separators so it also protects the folder on Windows.
"""

import json
import re
import sys

try:
    path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "") or ""
except (ValueError, AttributeError):
    sys.exit(0)

parts = [p for p in re.split(r"[\\/]+", path) if p]
if "manim" in parts and "manimgen" not in parts:
    print(
        "[Hook] BLOCKED: manim/ is the read-only ManimGL source. Edit manimgen/ instead.",
        file=sys.stderr,
    )
    sys.exit(2)
