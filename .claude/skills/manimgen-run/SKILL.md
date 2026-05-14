---
name: manimgen-run
description: Use this skill whenever the user wants to run the manimgen pipeline — generating a video from a topic string or PDF. Triggers on phrases like "run manimgen", "generate a video on X", "run the pipeline on X", "let's test the pipeline", "run it on X", or any time the user wants to produce an animated video via manimgen. Also use when the pipeline fails to start due to import errors, missing module errors, or CLI not found.
---

# ManimGen Pipeline Runner

This skill handles the full ritual of running the manimgen pipeline reliably: verifying the install, fixing it if broken, loading the API key, and launching the run.

## Paths (never guess these)

- **Project root (setup.py lives here):** `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen`
- **Source package:** `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/`
- **Env file (API keys):** `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/.env`
- **Config:** `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/config.yaml`

## Step 1 — Verify the install

```bash
manimgen --help 2>&1 | head -3
```

If this prints the usage line, skip to Step 3.

If it fails with `ModuleNotFoundError: No module named 'manimgen'`, go to Step 2.

## Step 2 — Fix the editable install

The editable install breaks when the package mapping is stale. Fix it with a clean reinstall:

```bash
pip uninstall manimgen -y -q
pip install -e /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen -q
manimgen --help 2>&1 | head -3
```

**Why this happens:** `setup.py` uses `package_dir={"": "manimgen"}` so `find_packages` roots at `manimgen/manimgen/`. If pip's editable finder cache is stale, the mapping breaks. A clean reinstall regenerates it.

If it still fails after reinstall, check `setup.py` hasn't regressed:
```bash
grep -n "package_dir\|find_packages" /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/setup.py
```
It must contain both `package_dir={"": "manimgen"}` and `find_packages("manimgen", ...)`.

## Step 3 — Load API key

Read the key from `.env` — do not ask the user for it:

```bash
grep GEMINI_API_KEY /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/.env | cut -d= -f2
```

## Step 4 — Run the pipeline

For a topic string:
```bash
GEMINI_API_KEY=<key> manimgen "<topic>"
```

For a PDF:
```bash
GEMINI_API_KEY=<key> manimgen --pdf <path-to-pdf>
```

To resume from a cached plan:
```bash
GEMINI_API_KEY=<key> manimgen --resume
```

Tell the user to paste this command into their terminal with `! ` prefix so output streams live into the conversation.

## Step 5 — After the run

Once the run completes (or if the user pastes output showing it's done), invoke the `manimgen-video-review` skill to review the rendered video.

## Common failures and fixes

| Error | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'manimgen'` | Step 2 — clean reinstall |
| `zsh: parse error near '&&'` | User pasted a multiline command — give it as a single line |
| `GEMINI_API_KEY not set` | Re-read from `.env`, export explicitly |
| Pipeline hangs silently | PR #13 (timeout+retry) must be on main — check `git log --oneline -3` |
| `manimgl` render fails | Check `manimgen/output/logs/` for the error, then invoke `manimgen-test-fix` |
