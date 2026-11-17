"""PostToolUse hook: format and lint an edited Python file, report what remains.

Reads the hook JSON from stdin (tool_input.file_path). Never blocks: problems
are printed so they show up in the session. Silent when ruff is not installed.
"""

import json
import py_compile
import shutil
import subprocess
import sys

try:
    path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "") or ""
except (ValueError, AttributeError):
    sys.exit(0)

if not path.endswith(".py"):
    sys.exit(0)

ruff = shutil.which("ruff")
if ruff:
    for args in (["format", path], ["check", "--fix", path]):
        subprocess.run([ruff, *args], capture_output=True, timeout=60, check=False)

try:
    py_compile.compile(path, doraise=True)
except py_compile.PyCompileError as exc:
    print(f"[Hook] SYNTAX ERROR in {path}:\n{exc.msg}", file=sys.stderr)
except OSError:
    sys.exit(0)

if ruff:
    result = subprocess.run(
        [ruff, "check", path], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode != 0:
        print(
            f"[Hook] ruff still reports issues in {path} after autofix:\n{result.stdout}",
            file=sys.stderr,
        )
