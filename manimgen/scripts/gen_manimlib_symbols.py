"""Regenerate manimgen/validator/manimlib_symbols.json.

Run under a virtual display when headless:

    xvfb-run -a -s "-screen 0 1920x1080x24 -ac" python3 scripts/gen_manimlib_symbols.py

The table is the set of public names ``from manimlib import *`` binds, which is
what a generated scene can reference without importing anything else.
"""

import json
import sys
from pathlib import Path

OUT = (
    Path(__file__).resolve().parents[1]
    / "manimgen"
    / "validator"
    / "manimlib_symbols.json"
)


def live_star_import_names() -> list[str]:
    """Names bound by ``from manimlib import *`` in a fresh namespace."""
    sys.argv = [sys.argv[0]]  # manimlib parses argv at import time
    ns: dict = {}
    exec("from manimlib import *", ns)  # noqa: S102
    return sorted(n for n in ns if not n.startswith("_"))


def main() -> int:
    import importlib.metadata as md

    names = live_star_import_names()
    payload = {"manimgl_version": md.version("manimgl"), "names": names}
    OUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {len(names)} names to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
