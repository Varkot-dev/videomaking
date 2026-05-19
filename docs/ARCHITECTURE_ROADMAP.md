# ManimGen Architecture Roadmap — 2 Weeks

**Date:** 2026-05-19
**Source:** 7-agent whole-codebase review + 4-agent grounded research (see
`docs/superpowers/specs/2026-05-19-codebase-analysis-roadmap-design.md`).
**North Star:** #3 extensibility + #1 video quality co-primary; #4 ship-ready
secondary; reliability/CI instrumental.

---

## 1. The one principle (anchors everything below)

> **The LLM proposes structure and taste. The deterministic layer computes
> and enforces every value it can compute. Reprompt the LLM only when the
> fix needs information or judgment the deterministic layer provably lacks.**

Four independent reviewers converged on the same root defect: the pipeline's
deterministic "constraint column" (`timing_verifier`, `codeguard`,
`frame_checker`, `segmenter`, `cue_parser`) **computes the right answers,
then hands them back to the LLM as advice instead of enforcing them.**
`CLAUDE.md` claims `timing_verifier` is "authoritative"; the code proves it
is advisory at every call site. This single architectural inversion is
*both* the dominant quality bug (#1: freeze-frame tails) *and* the dominant
extensibility hazard (#3: unsafe seams).

### The defect-classification table (the spine of the target architecture)

| Defect class | Fix is a pure function of (parsed artifact + known inputs)? | Correct strategy |
|---|---|---|
| Per-cue `self.wait()` residual | **Yes** — `cue_dur − Σ run_time` | **Compute deterministically. Never ask the LLM.** |
| Dynamic `run_time` at a cue boundary | Detection yes; fix needs a structural choice | Deterministic detect → **targeted** reprompt (not blind retry) |
| Community→GL symbol rename | **Yes** — table-driven | Deterministic autofix (codeguard already does this — keep) |
| Unknown ManimGL symbol | Detection yes; fix no | Deterministic detect (allowlist) → targeted reprompt |
| JSON schema / escaping | **Yes** — it's a grammar | Constrain at decode time (provider schema). Never repair. |
| Visual / layout / pedagogy | **No** — needs perception/judgment | Reprompt — this is where the LLM round-trip earns its cost |

Today rows 1, 2, and 5 are misclassified as "reprompt" problems. The roadmap
re-classifies them to "compute" or "detect-then-targeted-reprompt."

---

## 2. Target architecture

```
LLM owns:  pedagogy, narration, per-cue visual DESIGN, technique choice,
           semantic layout taste            (stochastic, open-ended — KEEP)

Deterministic layer owns (AUTHORITATIVE, not advisory):
  cue boundaries     ← cue_parser            (already pure)
  cue durations      ← segmenter             (already pure; ground truth from audio)
  per-cue wait fill  ← timing_verifier.resolve_fill   (NEW: CUE_FILL sentinel;
                         never asks LLM for the number; dynamic run_time at a
                         cue boundary → hard precheck error, never a silent 0)
  ManimGL API valid  ← codeguard allowlist precheck    (NEW: closed symbol
                         table from the pinned manimlib build; existing
                         denylist autofixes stay as the fast path)
  JSON schema        ← provider structured output      (replace the §-sentinel
                         + _escape_bad_backslashes string-repair heuristic)
  render quality gate← RenderQualityGate                (NEW: joins
                         timing_verifier ∧ frame_checker ∧ layout_checker;
                         runs on EVERY video-producing path — first-pass,
                         cache hit, retry — none can bypass it)

Orchestration:
  Section pipeline   ← ordered [Stage] over a typed SectionContext; cache +
                         timing gate + quality gate are STAGES, not inline
                         branches (today: cli._run_section, 164 lines, 6 jobs,
                         cache path silently bypasses every quality gate)
```

---

## 3. The seven highest-leverage structural changes (ranked)

1. **F5 tri-state fix (EMERGENCY, week 1 day 1).** `_eval_constant` returning
   `None` must propagate as `UNKNOWN`, not silently become `0` (waits) or
   `1.0` (run_times). Today a correct scene using `run_time=rt` is scored as a
   multi-second shortfall → false freeze → forced retry → the LLM rewrites a
   *correct* scene into a defective one. **This is an active regression bleed,
   not a latent risk.** ~2h, fully unit-testable, zero API cost. Prerequisite
   for #2.
2. **Wire `blocking_freezes` into ALL video-producing paths.** It is already
   tested and wired into `retry_scene` only. First-pass `validate_render` and
   the render-cache branch (`cli.py:251`) ship freeze videos without ever
   calling it. ~15 lines; highest gain-to-effort change in the entire backlog;
   closes the newly-discovered cache-path hole where *all* validation is
   skipped.
3. **CUE_FILL sentinel + deterministic residual resolver.** Director emits
   `self.wait(CUE_FILL)`; the resolver substitutes the exact residual via AST,
   never regex. Rolled out *additively* (sentinel path + existing regex
   `auto_fix_timing` as fallback) so no example scene or existing timing test
   breaks; shadow-flagged behind `MANIMGEN_TIMING_SENTINEL=1`. Kills the
   dominant quality bug at the root and removes the most fragile LLM
   responsibility.
4. **`RenderQualityGate` joining timing ∧ frame ∧ layout.** Re-enables the
   frozen-frame pixel signal (currently computed then discarded) *safely* by
   gating it on the timing model — frozen is hard *iff* timing confirms the
   narration for that cue already ended. One gate, every path, no bypass.
5. **codeguard denylist → allowlist precheck.** Generate a ManimGL symbol
   table by introspecting the *pinned, installed* `manimlib` (assert
   `manimlib.__file__` matches the pinned build, fail loud otherwise). Run
   **report-only / shadow** for the full 2 weeks — measure the
   currently-unmeasured rate of unseen-bad-API escapes; do NOT enforce this
   cycle (a false positive silently degrades a section to the bullet
   fallback). Enforcement is a post-roadmap PR gated on shadow data showing
   ~0 false positives against the `examples/` corpus.
6. **Provider structured output for planner JSON.** Adopt Gemini
   `response_schema` + Pydantic models; delete the `§` sentinel,
   `_reconstruct_latex`, and `_escape_bad_backslashes`. The `§`→`\` global
   replace is the only *data-integrity* defect found (silently corrupts any
   legitimate literal `§` in narration). Lock-in bounded by the existing
   3-branch provider switch in `llm.py`.
7. **`_run_section` → typed Section-stage seams.** Extract the 3 highest-value
   seams (`_generate_and_gate`, `_render_with_retry`, `_cut_and_mux`) as
   typed, individually-testable functions. NOT a full Stage framework yet
   (YAGNI). This is the #3 spine but the highest-blast-radius refactor — it
   must land *after* #2/#3 (they edit `_run_section`) and *after* the e2e
   safety net is repaired (it currently has zero automated guard).

---

## 4. Two-week phased plan

### Week 1 — Stop the bleeding; make the gate authoritative; make CI honest

| Phase | Work | Serves | Risk |
|---|---|---|---|
| W1-D1 | F5 tri-state fix (#1). Strip API keys from render subprocess env (CRITICAL security). Sanitize LLM-controlled `section['id']` path (CRITICAL security, parse-time). `pyproject.toml` pytest config; delete obsolete `--ignore` trio; fix the misleading e2e xfail comment (live booby-trap: names a non-existent mock target whose real site fires a paid call). | #1, sec, instrumental | Low |
| W1-D2 | Wire `blocking_freezes` into first-pass + cache paths (#2). Silent-failure batch with a shared 3-state result (mux-fail-no-narration, config-swallow, `layout_checker` ok-true-on-failure). | **#1**, #3 | Low–Med |
| W1-D3 | Provider structured output for planner JSON (#6); delete `§`/`_escape_bad_backslashes`. ManimGL symbol-table generator + shadow-mode logging (#5, no enforcement). | #1, #3 | Med |
| W1-D4 | Repair the e2e safety net: replace the dead xfail e2e test with 4–6 focused `cli.py` integration tests at the `chat()` + per-module `subprocess.run` seams. Remove `--maxfail=1` from CI (it structurally under-reports). | instrumental → unblocks #7 | Med |

### Week 2 — Make it correct-by-construction; build the seam; prove it

| Phase | Work | Serves | Risk |
|---|---|---|---|
| W2-D1–2 | CUE_FILL sentinel + deterministic resolver (#3), additive + shadow-flagged. Director prompt + 2–3 example scenes only; shadow-compare vs the regex fixer on real runs. | **#1+#3** | Med (mitigated by additive rollout) |
| W2-D2–3 | `RenderQualityGate` joining timing ∧ frame ∧ layout (#4); re-enable frozen detection gated on timing. Single gate on every path. | **#1** | Med |
| W2-D3–4 | Extract the 3 `_run_section` seams as typed functions (#7), after characterization tests on the cache-hit and all-muxed-skip fast paths. | **#3 spine** | Med-High (gated on W1-D4) |
| W2-D5 | Build the defect-rate measurement harness (fixed ~8–12 topic corpus; "video health score" = frozen-tail seconds, max A/V drift, placeholder-cue rate, black/clipped count, unverified-layout rate). Establish baseline so every prior change is *proven*, not claimed. | proves #1 | Low |

> Sequencing note: the harness (W2-D5) measures *across* the two weeks. Ideally
> its scoring script is stubbed in W1 so each change can be scored as it lands;
> the full corpus baseline is W2-D5.

---

## 5. What stays the LLM's job (do NOT deterministically replace)

Pedagogical decomposition and narration; the per-cue *visual design* (which
mobjects, what motion, color/composition); example/technique selection intent;
`layout_checker`'s semantic "does this look right" judgment. The error was
never "too much LLM" — it is that *deterministic arithmetic and API
membership* were left inside the LLM's responsibility while the layer that
knows the answer was wired as advice.

---

## 6. Post-roadmap (explicitly out of scope for these 2 weeks)

- codeguard allowlist **enforcement** flip (needs shadow data first).
- Full `Stage` pipeline framework (only after the 3 extracted seams prove out).
- Migrating all 30+ example scenes to the sentinel + deleting the regex
  `auto_fix_timing` fallback.
- Defense-in-depth `section['id']` helper at every sink (W1-D1 ships the
  parse-time CRITICAL stopgap; the resume-path bypass is a known, accepted
  one-cycle gap).
- Sandboxing the `manimgl` subprocess (CRITICAL F2 — architectural, large;
  the W1-D1 env-strip + id-sanitize buy down most of the exploit surface
  meanwhile).

---

## 7. References (research-grounded)

- Type-Constrained Code Generation with Language Models, PLDI 2025 (arXiv:2504.09246)
- Is Self-Repair a Silver Bullet for Code Generation?, ICLR 2024
- Guiding LLMs The Right Way: Fast, Non-Invasive Constrained Generation (arXiv:2403.06988)
- Gemini API Structured Outputs (ai.google.dev/gemini-api/docs/structured-output)
- pytest xfail / coverage anti-patterns (Ganssle)
