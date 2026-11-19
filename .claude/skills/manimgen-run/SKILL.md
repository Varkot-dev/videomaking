---
name: manimgen-run
description: Use this skill whenever the user wants to run the manimgen pipeline — generating a video from a topic string or PDF. Triggers on phrases like "run manimgen", "generate a video on X", "run the pipeline on X", "let's test the pipeline", "run it on X", or any time the user wants to produce an animated video via manimgen. Also use when the pipeline fails to start due to import errors, missing module errors, or CLI not found.
---

# ManimGen Pipeline Runner

This skill handles running the manimgen pipeline reliably: verifying the environment, fixing the install if it is broken, and launching the run.

`manimgen/CLAUDE.md` and the root `README.md` are the sources of truth. If anything here disagrees with them, they win and this file should be corrected.

## Paths (all relative to the repository root)

- **Project root (setup.py and config.yaml live here):** `manimgen/`
- **Source package:** `manimgen/manimgen/`
- **Optional API keys (only for the `anthropic` / `gemini` providers):** `manimgen/.env`

Never hardcode an absolute path: the repository lives in different places on macOS, Linux and the Windows PC.

## Step 1 — Run the env doctor

From `manimgen/`:

```bash
python3 scripts/env_doctor.py
```

Exit code 0 means the environment can render. Exit code 1 prints each blocking issue with its exact fix; apply the fix and re-run. On headless Linux, prefix commands with `xvfb-run -a`.

## Step 2 — Fix the editable install (only if the doctor reports the entrypoint)

From `manimgen/`:

```bash
pip install -e .
```

`setup.py` must use `find_packages(include=["manimgen", "manimgen.*"])` and must NOT set `package_dir`. A `package_dir={"": "manimgen"}` line is the old bug (KNOWN_ISSUES #3), not the fix.

## Step 3 — Run the pipeline

The default provider is `claude_cli` (Claude Code headless, `claude -p`). It needs no API key, only Claude Code installed and logged in.

From `manimgen/`:

```bash
MANIMGEN_MAX_RETRY_LLM_CALLS=2 manimgen "<topic>"
```

```bash
manimgen --pdf <path-to-pdf>
```

```bash
manimgen --resume
```

On Windows PowerShell set the variable first: `$env:MANIMGEN_MAX_RETRY_LLM_CALLS=2; manimgen "<topic>"`.

The `gemini` and `anthropic` providers bill per token and are blocked unless `MANIMGEN_ALLOW_PAID_API=1` is set. Do not switch providers unless the user asks.

## Step 4 — After the run

Once the run completes, invoke the `manimgen-video-review` skill. Never report a run as good from its exit code alone.

## Common failures and fixes

| Error | Fix |
|---|---|
| `No module named 'manimgen.cli'` | Step 2 |
| `No module named 'pkg_resources'` | `pip install "setuptools<81"` |
| `'claude' was not found on PATH` / not logged in | Install Claude Code, open a new terminal, run `claude` once (KNOWN_ISSUES #6) |
| Run stops after planning with a TTS error | edge-tts could not reach the speech service; see "Narration" in `manimgen/docs/KNOWN_ISSUES.md` (`--allow-silent` renders without audio) |
| `NoSuchDisplayException` on Linux | Run under `xvfb-run -a` |
| `manimgl` render fails | Read `manimgen/manimgen/output/logs/`, then invoke `manimgen-test-fix` if it is a code bug |
