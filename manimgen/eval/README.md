# eval/ — measuring Codeguard instead of estimating it

This directory exists to replace a prose estimate with a measurement.

## What's here

| path | purpose |
|---|---|
| `corpus/` | 57 labelled broken-scene cases, each with a provenance sidecar |
| `run_corpus.py` | runs Codeguard's real static repair path over the corpus, emits the resolution rate |
| `aggregate_logs.py` | computes the **same** metric from production evidence logs |
| `results/codeguard.json` | machine-readable measurement output |
| `results/codeguard.md` | human-readable summary |
| `results/fix_rule_inventory.md` | exact fix-rule count + regex-vs-AST mechanism audit |

## Reproduce

```bash
python3 eval/run_corpus.py                 # corpus measurement
python3 eval/aggregate_logs.py             # production measurement (needs real runs)
python3 -m pytest tests/test_run_corpus.py tests/test_evidence_log.py -q
```

## How resolution is scored

A case counts as resolved only when **both** hold:

1. the case's specific defect marker is gone from the repaired source, and
2. `validate_scene_code()` on the repaired code returns no errors.

Condition (2) alone is far too weak. `validate_scene_code` checks syntax, the
banned-pattern denylist, and design invariants — it has no idea that `x_length=`
or `DARK_GREY` are wrong, because those only fail at ManimGL render time. **22 of
the 57 cases pass `validate_scene_code` before any repair runs at all.** Scoring
on validation alone would hand Codeguard 22 free wins for defects it never
touched.

Condition (1) alone is also insufficient: it would miss a "fix" that removes the
defect while corrupting the file — the real #55 regression. Requiring both is
what makes the number mean something.

`run_corpus.py` additionally **refuses to score** any case whose probe already
passes on the unrepaired source, so a too-lenient probe fails loudly rather than
inflating the rate. Cases where the correct behavior is to change nothing are
labelled `regression_guard` and exempted explicitly.

## Reading the number honestly

The headline rate is always reported split by `provenance.source`, because a
corpus written by hand against the fix rules partly determines its own pass
rate. The `git-history` figure is the more trustworthy one; it is also the lower
one. See `results/codeguard.md`.

## Keeping it true

`manimgen/validator/evidence_log.py` persists precheck outcomes, shadow-check
hits, and A/V mismatches as JSONL (default `output/logs/codeguard_events.jsonl`,
disable with `MANIMGEN_EVIDENCE_LOG=0`). `aggregate_logs.py` reduces those to the
same validation-clean rate, so a real production run can confirm or refute the
corpus number rather than leaving it unfalsifiable.
