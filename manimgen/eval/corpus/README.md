# Codeguard failure corpus

Labelled broken-scene cases used to measure Codeguard's real static-repair
resolution rate (`eval/run_corpus.py`).

## Layout

Each case is two files:

- `<case_id>.py`   — the broken scene source, exactly as the failure mode appears
- `<case_id>.json` — sidecar metadata

## Sidecar schema

| field | meaning |
|---|---|
| `case_id` | matches the `.py` basename |
| `failure_mode` | short slug, e.g. `unexpected_kwarg_checkerboard_colors` |
| `error_class` | the Python exception class the real render raised (`TypeError`, `NameError`, `AttributeError`, `SyntaxError`), or `LayoutDefect` for non-crashing visual defects |
| `provenance.source` | `git-history` \| `render-log` \| `derived-from-fix-rule` |
| `provenance.ref` | commit hash, log path, or `codeguard.py:<symbol>` the case was derived from |
| `provenance.evidence` | verbatim traceback line or commit-message quote that documents this failure really happened |
| `stderr` | the traceback text fed to `apply_error_aware_fixes` (empty when the failure is caught proactively, with no render attempt) |
| `expected_outcome` | `repairable` (Codeguard claims a rule for this), `not_repairable` (documented as out of scope / detection-only), or `regression_guard` (valid code that Codeguard must leave *untouched* — see `gh11`) |
| `notes` | free text |

## Provenance policy

`source` is the single most important field. It records whether a case is a
*real observed failure* or something written by hand.

- **`git-history`** — the failure is documented verbatim in the repository's own
  history: a traceback quoted in `docs/ROOT_CAUSE_llm_failures.md` (added in
  `f732f49`, itself transcribed from `output/logs/Section0*_attempt*.log` during
  a real run), or a commit message describing the generated code that forced a
  fallback. The raw scene files were gitignored and are gone, so the *scene body*
  around the failing line is reconstructed minimally — but the failing construct
  itself is verbatim from the record.
- **`derived-from-fix-rule`** — no surviving traceback. The case is written from
  the fix rule's own docstring/comment in `codeguard.py`, which describes what
  real LLM output gets wrong. These are still grounded in a developer's
  observation of real output, but they are *not* independently attested, and a
  corpus written against the rules partly determines its own pass rate.

The resolution rate must always be reported split by this field. See
`eval/results/codeguard.md`.
