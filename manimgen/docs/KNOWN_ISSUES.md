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
**Other providers:** only Gemini has native JSON mode. For `claude_cli`,
`anthropic` and `ollama`, `chat(json_mode=True)` relies on the prompt and strips
a surrounding markdown `json` code fence before the planner parses the reply.

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

## 6. `claude -p` not found / not logged in (default `claude_cli` provider)
**Symptom:** every LLM call fails with `LLM_PROVIDER=claude_cli but 'claude' was
not found on PATH`, or `claude -p failed: ...` mentioning login/authentication.
**Cause:** the default provider shells out to Claude Code. It must be installed,
on PATH, and logged in once interactively. On Windows the native installer puts
`claude.exe` under `%USERPROFILE%\.local\bin`, which a fresh terminal may not
have on PATH yet.
**Fix:** install Claude Code, open a new terminal, run `claude` once and log in.
If it is installed somewhere off PATH, set `llm.claude_cli_path` in
`config.yaml` to the full path. To use another provider instead, set
`LLM_PROVIDER=ollama` (free), or `anthropic|gemini` together with
`MANIMGEN_ALLOW_PAID_API=1` (they bill per token and are blocked otherwise).
**Note:** `ANTHROPIC_API_KEY` is stripped from the `claude` subprocess on
purpose, so a key in `.env` never silently moves `claude_cli` calls onto
per-token API billing. Hitting the Claude plan's usage limit also shows up as
`claude -p failed`; wait for the limit to reset and use `manimgen --resume`.
**Guard:** `check_claude_cli()`.

## 7. `ffmpeg` / `ffprobe` / `manimgl` missing from PATH
**Symptom:** renders, cuts or muxes fail with `FileNotFoundError` for one of
these tools.
**Cause:** usually FFmpeg was unzipped (Windows) but its `bin` folder was never
added to PATH, or the venv holding `manimgl` is not active.
**Fix:** add the FFmpeg `bin` folder to PATH (on Windows without admin: "Edit
environment variables for your account", user `Path`), reopen the terminal, and
activate the venv (or call `.venv\Scripts\python.exe` directly).
**Guard:** `check_render_toolchain()`.

---

## Environment baseline, macOS (verified working 2026-05-25)
- Python 3.13 framework build at `/Library/Frameworks/Python.framework/Versions/3.13`
- manimgl 1.7.2 (non-editable), setuptools 80.x, manimgen 0.1.0 (editable, parent path)
- `manimgl` + `ffmpeg` on PATH
- macOS has **no** `timeout` command (no GNU coreutils) — don't wrap renders in `timeout`.

## Windows notes (no admin rights)
- Full per-user setup (Python 3.11 "for current user", Git, FFmpeg zip, Claude
  Code via `irm https://claude.ai/install.ps1 | iex`, optional MiKTeX "only for
  me") is in the root README.md, "Setup: Windows 10 / 11".
- If `.venv\Scripts\activate` is blocked ("running scripts is disabled"), run
  `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` (no admin needed). If
  group policy still blocks it, call `.venv\Scripts\python.exe` directly.
- The render environment (`validator/env.py`) passes through the Windows
  variables child processes need (`SYSTEMROOT`, `PATHEXT`, `TEMP`, etc.) and finds
  `latex` through PATH, so MiKTeX works once its `bin` folder is on PATH.
