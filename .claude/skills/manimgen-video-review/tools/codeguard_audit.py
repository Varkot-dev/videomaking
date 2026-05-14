"""
Run codeguard's layout-smell checks against every section_*.py in the scenes
output directory. Emits a JSON report grouping warnings by section so the
video-review skill knows which sections to inspect most carefully.

Layered audit philosophy: the cheap deterministic check runs first. If it
catches a defect, you don't need to extract frames to know that section is
suspicious — the source code itself is the evidence.

Output schema:
{
  "scenes_dir": "/abs/path/to/scenes",
  "sections": [
    {"id": 1, "file": "section_01.py", "warnings": [...]},
    ...
  ],
  "total_warnings": 7
}

Usage:
  python3 codeguard_audit.py [scenes_dir]

Default scenes_dir: manimgen/manimgen/output/scenes (relative to project root).
"""
from __future__ import annotations

import json
import os
import re
import sys


_DEFAULT_SCENES_DIR = (
    "/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/output/scenes"
)
_PACKAGE_ROOT = "/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen"

_SECTION_RE = re.compile(r"section_(\d+)\.py$")


def _import_check_layout_smells():
    """Import _check_layout_smells from the project's codeguard module.

    The video-review skill lives in .claude/skills/, outside the package.
    Add the package root to sys.path on demand so the import works regardless
    of CWD.
    """
    if _PACKAGE_ROOT not in sys.path:
        sys.path.insert(0, _PACKAGE_ROOT)
    from manimgen.validator.codeguard import _check_layout_smells  # noqa: E402
    return _check_layout_smells


def audit_scenes(scenes_dir: str) -> dict:
    check = _import_check_layout_smells()

    if not os.path.isdir(scenes_dir):
        return {
            "scenes_dir": scenes_dir,
            "sections": [],
            "total_warnings": 0,
            "error": f"scenes dir does not exist: {scenes_dir}",
        }

    sections: list[dict] = []
    total = 0
    for fname in sorted(os.listdir(scenes_dir)):
        m = _SECTION_RE.match(fname)
        if not m:
            continue
        path = os.path.join(scenes_dir, fname)
        try:
            with open(path) as f:
                code = f.read()
        except OSError as exc:
            sections.append({
                "id": int(m.group(1)),
                "file": fname,
                "warnings": [],
                "read_error": str(exc),
            })
            continue
        warnings = check(code)
        sections.append({
            "id": int(m.group(1)),
            "file": fname,
            "warnings": warnings,
        })
        total += len(warnings)

    return {
        "scenes_dir": scenes_dir,
        "sections": sections,
        "total_warnings": total,
    }


def main() -> int:
    scenes_dir = sys.argv[1] if len(sys.argv) > 1 else _DEFAULT_SCENES_DIR
    report = audit_scenes(scenes_dir)
    print(json.dumps(report, indent=2))
    return 0 if "error" not in report else 1


if __name__ == "__main__":
    raise SystemExit(main())
