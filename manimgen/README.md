# ManimGen

The full documentation lives at the repository root: **[../README.md](../README.md)**.

That is the file GitHub renders on the project page, so it is the one that gets
read and the one kept current.

This package previously carried a second, near-duplicate README. The two
drifted — they published different test counts, and neither matched reality —
and, worse, the copy being maintained was the one nobody saw.
`tests/test_docs_accuracy.py` now checks the root document too, so that class of
drift fails the build instead of sitting unnoticed.

## Quick reference

Run everything from this folder (the one holding `setup.py` and `config.yaml`).

```bash
pip install -r requirements-dev.txt   # runtime deps + pytest, pytest-mock, hypothesis, ruff
pip install -e .                      # the manimgen / manimgen-edit commands
claude                                # once: log in to Claude Code (default LLM provider)

python3 -m pytest -q          # full suite, fully mocked, no API key needed
manimgen "binary search"      # generate a video from a topic
manimgen --pdf lecture.pdf    # or from a PDF
```

The default LLM provider is `claude_cli`: each call runs `claude -p` and counts
against your Claude plan's usage limits, with no API key. Pick the model with
`llm.claude_cli_model` in `config.yaml` (`sonnet` by default, or `opus`), or
switch provider with `LLM_PROVIDER=ollama` (local, free). The per-token providers
`anthropic` and `gemini` are blocked unless `MANIMGEN_ALLOW_PAID_API=1` is also
set, so nothing bills against an API key by accident.

See [../README.md](../README.md) for the pipeline architecture, the Codeguard
repair harness, LLM provider details, macOS/Linux and Windows (no admin) setup,
and the cost model.
