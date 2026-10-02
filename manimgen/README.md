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
set, so nothing bills against an API key by accident. The pipeline also stops
before a plan can run into "extra usage" (overage) billing, and
`python scripts/check_billing.py` (run from `manimgen/`) makes one tiny call and
reports whether any charge is possible. Tune the stop point with
`MANIMGEN_MAX_PLAN_UTILIZATION` (default `0.90`).

**Models by role.** Each LLM call carries a role (`researcher`, `planner`,
`planner_pdf`, `critic`, `cue_refill`, `director`, `error_fix`, `visual_fix`,
`layout_check`). The optional `llm.models:` block in `config.yaml` maps a role
to `haiku`, `sonnet`, `opus` or a full model ID, and `MANIMGEN_MODEL_<ROLE>`
(for example `MANIMGEN_MODEL_DIRECTOR=opus`) overrides it for one run. Nothing is
changed by default: every role uses `llm.claude_cli_model`, and an unknown role
or empty value falls back to it. Tiers worth trialling, one role at a time:
`planner` on opus; `researcher`, `critic`, `director`, `error_fix` and
`visual_fix` on sonnet; `cue_refill` and `layout_check` on haiku. Judge each
change with the usage ledger over about 10 topics.

**Usage ledger.** Every call appends one JSON line to
`<logs_dir>/llm_usage.jsonl` (role, model, provider, seconds, input and output
tokens, an API-price cost equivalent, and 5-hour and 7-day plan utilization
before and after). `manimgen.llm.usage_summary()` returns a per-role table and
the plan-utilization change over the run, so you can see what share of the
5-hour allowance one video costs.

See [../README.md](../README.md) for the pipeline architecture, the Codeguard
repair harness, LLM provider details, macOS/Linux and Windows (no admin) setup,
and the cost model.
