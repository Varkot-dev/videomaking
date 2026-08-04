# Known Issues & Recurring Landmines

Every entry here is a real failure we diagnosed and fixed. Each one has an
**active guard** in `scripts/env_doctor.py` (run at session start via the
`.claude/settings.json` SessionStart hook) so it cannot silently recur.

> When you hit a new recurring setup/environment failure: fix it, add a check to
> `scripts/env_doctor.py`, and document it here. Enforcement > memory.

---

## 1. `import manimlib` fails — moved-project editable install
**Symptom:** `ModuleNotFoundError: No module named 'manimlib'` even though the
`manimgl` binary exists.
**Cause:** manimgl was an *editable* install pointing at the project's old path
(`~/Projects/3Blue1Brown/manim`). The project moved to `~/videomaking/`, so the
editable path finder points at a dead directory.
**Fix:** `pip install 'manimgl==1.7.2'` (non-editable, from PyPI).
**Guard:** `check_manimlib_imports()`.

## 2. `pkg_resources` missing — setuptools 81+
**Symptom:** `ModuleNotFoundError: No module named 'pkg_resources'` when importing
manimlib.
**Cause:** manimgl 1.7.2's `manimlib/__init__.py` does `import pkg_resources`,
which setuptools **81+ removed**.
**Fix:** `pip install 'setuptools<81'` (manimlib only uses it to read its own
version; the deprecation `UserWarning` is harmless).
**Guard:** `check_setuptools_has_pkg_resources()`.

## 3. `manimgen.cli` not importable — editable `.pth` points at wrong dir
**Symptom:** `manimgen` binary fails with `No module named 'manimgen.cli'`, but
`import manimgen` works *inside* the project dir.
**Cause:** the editable `.pth` pointed at the package dir
(`.../videomaking/manimgen/manimgen`) instead of its **parent**, so Python
resolved a namespace package one level too deep.
**Fix:** point `site-packages/__editable__.manimgen-0.1.0.pth` at the parent
(`<repo-root>`), or `pip install -e .` from the
project root.
**Guard:** `check_manimgen_entrypoint()` (imports from a neutral cwd so it can't
be masked).

## 4. Planner `JSONDecodeError` — unconstrained LLM JSON
**Symptom:** pipeline crashes in `plan_lesson()` with
`json.decoder.JSONDecodeError: Expecting ',' delimiter`.
**Cause:** the planner asked Gemini for JSON in the prompt but did not use
structured output, so an occasional malformed plan (missing comma, etc.) slipped
past the backslash-only regex repair.
**Fix:** pass `json_mode=True` on planner `chat()` calls →
`response_mime_type="application/json"` so Gemini emits guaranteed-valid JSON.
**Guard:** `check_planner_uses_json_mode()`.

## 5. `--fps` crashes manimgl 1.7.2 — `int / str`
**Symptom:** EVERY render aborts with
`TypeError: unsupported operand type(s) for /: 'int' and 'str'` at
`1 / self.camera.fps` in `manimlib/scene/scene.py`.
**Cause:** manimgl 1.7.2 declares `--fps` *without* `type=int`; `config.py` then
assigns the raw string into `camera_config.fps`. The flag is irreparably broken
in this build.
**Fix:** never pass `--fps`. Render via
`validator/render_command.build_manimgl_command()`, which omits it. manimgl
renders at its bundled default (30fps); the assembler normalizes the final cut.
**Guard:** `check_no_broken_fps_flag()` (scans render code for `"--fps"`).

---

## Environment baseline (verified working 2026-05-25)
- Python 3.13 framework build at `/Library/Frameworks/Python.framework/Versions/3.13`
- manimgl 1.7.2 (non-editable), setuptools 80.x, manimgen 0.1.0 (editable, parent path)
- `manimgl` + `ffmpeg` on PATH
- macOS has **no** `timeout` command (no GNU coreutils) — don't wrap renders in `timeout`.
