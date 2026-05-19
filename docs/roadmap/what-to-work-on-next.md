# 3–4 Day Prioritized Backlog (Ambitious — a menu to pull from)

**Date:** 2026-05-19
**North Star:** #3 extensibility + #1 video quality co-primary; #4 ship-ready
secondary; reliability instrumental.
**Format:** dependency-ordered. Each item: scope, exact files, approach (with
the research-backed recommendation + rejected alternatives), effort, risk,
test strategy, North-Star tag, the finding it closes.

> This is an *ambitious* backlog — it assumes smooth execution. Pull from the
> top. The dependency edges (bottom of doc) are hard constraints; the rest is
> priority order.

---

## Day 1 — CRITICAL security + the active quality bleed + CI honesty (all parallelizable)

### PR-1 · Fix F5: timing tri-state (`UNKNOWN`, not silent 0) — `#1` EMERGENCY
- **Why first:** the only finding *actively breaking correct scenes right
  now*. `_eval_constant`→`None` becomes `0` for waits / `1.0` for run_times →
  a correct scene with `run_time=rt` is scored as a multi-second freeze →
  forced retry → LLM rewrites good code into bad. Active regression bleed.
- **Files:** `validator/timing_verifier.py` (`_eval_constant`, `_get_run_time`,
  `_get_wait_duration`, `_time_for_statements`, `blocking_freezes`).
- **Approach:** make the constant evaluators return a tri-state
  (`float | UNKNOWN`). `blocking_freezes` must REFUSE to block when a cue
  contains `UNKNOWN` animation time (cannot prove a freeze → do not force a
  destructive retry). Emit a distinct precheck signal instead.
- **Effort:** S (~2h). **Risk:** Low (narrows behavior; unit-testable).
- **Tests:** unit — scene with `run_time=rt` (dynamic) → no false freeze;
  scene with constant timing → unchanged verdicts; `UNKNOWN` in a cue →
  not blocked. Regenerate timing golden fixtures.
- **Closes:** F5 (CRITICAL).

### PR-2 · Strip API keys from the `manimgl` subprocess env — `sec` CRITICAL
- **Files:** `validator/env.py` only (`get_render_env`, 19 lines).
- **Approach (recommended: allowlist, B1):** build the render env from an
  explicit allowlist (PATH, HOME, TMPDIR, LANG, LC_*, DISPLAY, TEXLIVE_*,
  PYTHON*, plus a `MANIMGEN_RENDER_ENV_EXTRA` comma-passthrough escape hatch).
  *Rejected: denylist-pattern strip — rots exactly like the codeguard
  denylist; a future `GOOGLE_APPLICATION_CREDENTIALS` leaks.*
- **Effort:** S (~half day incl. one enumeration render to seed the
  allowlist). **Risk:** Low-Med (under-enumeration → cryptic LaTeX failure;
  mitigated by seeding from observed working set + escape hatch).
- **Tests:** new `test_env.py` — `GEMINI_API_KEY`/`ANTHROPIC_API_KEY` absent
  from `get_render_env()` when set in `os.environ`; PATH has tex bin;
  required render vars present.
- **Closes:** F3 (CRITICAL). Generated scene code can no longer exfiltrate
  keys via `os.environ`.

### PR-3 · Sanitize LLM-controlled `section['id']` filesystem path — `sec` CRITICAL
- **Files:** `utils.py` (new `safe_section_id` next to `section_class_name`),
  `planner/lesson_planner.py` (`_extract_cues` — coerce at parse time).
- **Approach (recommended: C1 now, C2 fast-follow):** coerce, don't reject
  (`re.sub(r'[^a-z0-9_]', '_', id.lower())[:64]`; empty → `f"section_{idx:02d}"`,
  the already-battle-tested default). Append a short stable hash of the
  original id when coercion changed it (prevents `a-b`/`a_b` collision →
  wrong audio on wrong video). *Known accepted gap: the `--resume` path reads
  cached `plan.json` without re-running `_extract_cues` → C1-alone is
  incomplete by design; C2 (defense-in-depth helper at every os.path.join
  sink) is a fast-follow next cycle.*
- **Sinks:** `cli.py:52`, `cli.py:85`, `fallback.py:49`,
  `scene_generator.py:205` (last two write `.py` that manimgl then executes —
  RCE-adjacent).
- **Effort:** S (~half day). **Risk:** Med (collision; mitigated by hash
  suffix). **Tests:** unit + hypothesis (already a dep) — `../../etc/x`,
  `a/b`, `...`, empty, unicode → all coerced to `^[a-z0-9_]{1,64}$`;
  integration — malicious plan.json writes nothing outside `output/`.
- **Closes:** F1 (CRITICAL).

### PR-4 · CI/test honesty trio — `instrumental` (unblocks PR-9, de-risks all)
- **Files:** new `pyproject.toml` `[tool.pytest.ini_options]`; `CLAUDE.md`
  (delete the obsolete `--ignore` trio at lines ~10/156/416);
  `tests/test_pipeline_e2e.py` (fix the misleading xfail comment ONLY);
  `.github/workflows/ci.yml` (remove `--maxfail=1` from the `test` job).
- **Approach:** register `llm`/`network`/`slow` markers (satisfies the
  existing `--strict-markers`); `addopts = -m "not llm"` so the default
  `pytest` run == CI run, byte-for-byte (the `--ignore`d files are *safe* —
  fully mocked — hiding them trains distrust of the local command and already
  caused one incident). Fix the e2e comment: it says mock
  `manimgen.cli.check_layout` which **does not exist**; the real call is
  `manimgen.validator.retry.check_layout` and naively un-xfailing fires a
  *paid Gemini call* — this is a live booby-trap.
- **Effort:** S–M. **Risk:** Low (config + docs; the marker default must
  deselect the real LLM-calling tests or CI burns credits — explicit test).
- **Closes:** F13 (HIGH) partial — the cheap, structural-dishonesty half.

---

## Day 2 — Make the freeze gate authoritative + stop shipping broken video

### PR-5 · Wire `blocking_freezes` into ALL video-producing paths — `#1` HIGHEST GAIN/EFFORT
- **Why:** the dominant viewer complaint (1–8s dead screens) is already
  detected and quantified by tested code — it is simply **not called on 2 of
  3 ship paths**. First-pass `validate_render` and the render-cache branch
  (`cli.py:251-256`, which bypasses *every* quality gate — newly discovered)
  never call it.
- **Files:** `cli.py` (first-pass `validate_render` branch ~286-298; cache
  branch ~251-256), `validator/render_validator.py` (add
  `blocking_freezes(verify_timing(...))`; classify freeze as `"hard"`).
- **Approach:** call the existing `blocking_freezes(verify_timing(code,
  cue_durations))` on both bypassed paths; a positive result is a hard
  failure → routes to retry. Depends on PR-1 (UNKNOWN must not be treated as a
  freeze, else this storms retries on correct scenes).
- **Effort:** Low (~15 lines). **Risk:** Low (logic exists and is tested).
- **Tests:** integration — first-pass render with a >2.5s freeze tail enters
  retry (not ships); cache-hit with a stale-but-fresh-hash freeze is
  re-validated. **Closes:** F6 (CRITICAL), finding 1c.
- **Tag:** `#1`. *Single highest gain-to-effort change in the backlog.*

### PR-6 · Silent-failure batch + shared 3-state result — `#1`/`#3`
- **Files:** `cli.py` (mux-fail ~340: stop `produced.append(cue_clip)` of a
  narration-less clip), `paths.py:54` + `cli.py:28` (config swallow → log +
  WARNING, never silent `{}`), `validator/layout_checker.py:163-171`
  (`ok=True`-on-LLM-failure → distinct `skipped` the retry loop does NOT treat
  as pass).
- **Approach (recommended: G3 hybrid):** introduce a shared 3-state
  (`pass | skipped/unknown | fail`) modeled on the existing
  `render_validator.ValidationResult.severity`. The `layout_checker` fix MUST
  land with/before PR-7 — otherwise PR-7's hard gate is undermined by a vision
  check that returns `ok=True` on its own failures.
- **Effort:** M (~1 day). **Risk:** Med — making mux-failure loud turns
  previously-"successful" (silently broken) runs into visible failures (this
  is *correct* but looks like a regression); pair the raise with a
  retry-once-then-mark-section-failed policy, never append a silent clip.
- **Tests:** fault injection — mux raises → section retried/marked, never
  silently appended; bad config → logs+WARNING; `layout_checker` LLM raises →
  `skipped` ≠ pass. **Closes:** F7, F8, F14.

---

## Day 3 — Correct-by-construction timing + the extensibility-debt headline

### PR-7 · CUE_FILL sentinel + deterministic residual resolver — `#1`+`#3`
- **Why:** kills the dominant quality bug at the root. The residual is pure
  arithmetic (`cue_dur − Σ run_time`) — it should never be the LLM's to get
  wrong. Replaces brittle regex/`rfind` surgery in `auto_fix_timing`.
- **Files:** `validator/timing_verifier.py` (new `resolve_fill`),
  `generator/prompts/director_system.md`, 2–3 `examples/*.py` only,
  `test_timing_verifier.py`.
- **Approach (recommended: A3 additive + shadow):** Director emits one
  `self.wait(CUE_FILL)` per cue; resolver AST-substitutes the exact value.
  Keep the existing regex `auto_fix_timing` as the fallback for
  scenes/examples that don't use the sentinel → **no example must change, no
  existing timing test breaks.** Behind `MANIMGEN_TIMING_SENTINEL=1`; shadow
  (log "would substitute X" vs the regex fixer) on real runs, then flip.
  *Rejected: full rewrite A1 — couples 519 tests + 30 examples + the
  dominant-bug path in one L PR.* Depends on PR-1 (the resolver needs the
  non-fill sum to be correct-or-provably-UNKNOWN).
- **Effort:** M–L (~1.5d). **Risk:** Med — sentinel inside a loop body →
  N× substitution → massive overrun; resolver MUST detect sentinel-in-loop
  and route to `blocking_freezes`, never silently mis-fill.
- **Tests:** AST unit — sentinel present → exact substitution; absent →
  defined fallback (never silent 0); in loop → rejected. Property test
  (hypothesis): random anim sequences → post-resolve `computed ≈ expected`.
- **Closes:** F4 (CRITICAL).

### PR-8 · ManimGL symbol-table generator + SHADOW-only allowlist — `#3`
- **Why:** codeguard is a ~30-pattern reactive denylist; ManimGL legality is
  a closed set → an allowlist is the correct shape. Headline #3 tech-debt
  kill.
- **Files:** new `validator/manimgl_symbols.py` (introspect the *pinned,
  installed* `manimlib`; assert `manimlib.__file__` matches the pinned build,
  fail loud), `codeguard.py` (shadow-log "allowlist would block X; outcome
  Y").
- **Approach (recommended: D3 shadow-only this cycle):** generate a
  checked-in symbol snapshot regenerated/diffed by a test; run report-only
  alongside the denylist. **Do NOT enforce this cycle** — a false positive
  silently degrades a section to the bullet fallback; enforcement is a
  post-roadmap PR gated on shadow data showing ~0 false positives vs the
  `examples/` corpus. Keep codeguard's autofix rewrites (orthogonal, valuable).
- **Effort:** M–L (~1d for generator + shadow harness). **Risk:** Low
  (shadow cannot block anything). **Tests:** allowlist over all `examples/*.py`
  (known-good) → 0 flags; over `output/logs/*_attempt*.py` (known-bad) → flags
  the real errors. **Closes:** F9 (HIGH) — measurement half.

---

## Day 3–4 — The extensibility spine (gated, last on purpose)

### PR-9 · Extract 3 `_run_section` seams as typed functions — `#3` SPINE
- **Why:** `_run_section` is 164 lines / 6 jobs with no interface — the seam
  that makes every future feature unsafe to add. The cache fast-path silently
  bypasses every quality gate (a new contributor adding a check wouldn't know
  to add it in two places).
- **Files:** `cli.py` — extract `_generate_and_gate(section, cue_durations)`,
  `_render_with_retry(...)`, `_cut_and_mux(...)` as typed functions over a
  small `SectionContext`. *Rejected: full Stage framework (E1) — YAGNI; the
  3 seams deliver most of the #3 value at M effort.*
- **Effort:** M (~1.5d incl. characterization tests). **Risk:** Med-High —
  the ONLY e2e test is xfail-strict (zero automated safety net). **Hard
  dependencies: must land AFTER PR-7 + PR-6 (both edit `_run_section`) AND
  after PR-4's e2e replacement gives a safety net.** Characterization-test
  the cache-hit and all-muxed-skip fast paths *before* extracting (a naive
  extraction drops the resume/skip fast-paths → full re-render).
- **Tests:** characterization tests captured pre-refactor, must stay green.
- **Closes:** F10 (HIGH).

---

## Stretch (pull in if ahead — not on the critical path)

- **PR-S1 · Re-derive `cue_word_indices` from edge-tts WordBoundary** (`#1`,
  M) — removes the silent A/V desync (CLAUDE.md known issue #6); off-by-N is
  currently unguarded. `cue_parser` + `segmenter` seam.
- **PR-S2 · Per-segment narration as primary cue-visual input** (`#1`, M) —
  eliminates the `_refill→None` "animate segment 2 of 4" placeholder; uses
  the existing `_segment_narration`. Closes F12.
- **PR-S3 · Defect-rate measurement harness** (`proves #1`, M-H) — fixed
  ~8–12 topic corpus; "video health score" (frozen-tail seconds, max A/V
  drift, placeholder-cue rate, black/clipped count, unverified-layout rate).
  Build the scoring stub early so every PR above can be scored as it lands.
- **PR-S4 · Provider structured output for planner JSON** (`#1`/`#3`, M) —
  Gemini `response_schema` + Pydantic; delete the `§` sentinel (the only
  *data-integrity* defect: silently corrupts literal `§` in narration).
- **PR-S5 · Editor XSS sink + `request.json` guard + `safe_title` cap**
  (`#4`, S) — `editor.html` `clip.id`→`innerHTML`; `server.py` `request.json`
  None → 500; unbounded title. Pre-demo polish.
- **PR-S6 · Quality cleanup** (`#3`, S) — config caching (re-parsed every LLM
  call), `voice: str = None` type-lie, missing muxer subprocess timeouts,
  3× duplicated segment-construction, dead `_rendered_section_path`.

---

## Hard dependency edges (these are constraints, not preferences)

```
PR-1 (F5 tri-state) ───────────► PR-5 (blocking_freezes everywhere)
PR-1 ──────────────────────────► PR-7 (sentinel resolver)
PR-6 (layout_checker skip≠pass) ─► PR-7  (else PR-7's gate is undermined)
PR-7, PR-6 (both edit _run_section) ─► PR-9
PR-4 (e2e safety net) ──────────► PR-9  (PR-9 has no other automated guard)
PR-2, PR-3, PR-4, PR-8  — fully independent, parallelizable
PR-9 must be LAST of the core set; PR-8 stays shadow-only this cycle
```

**North-Star read:** Day 1 buys down CRITICAL security + the active quality
bleed + CI dishonesty cheaply and in parallel. Day 2 makes the freeze gate
authoritative (the #1 win, lowest effort). Day 3 delivers the two co-primary
headlines — correct-by-construction timing (PR-7, #1) and the denylist-debt
kill (PR-8, #3, de-risked to shadow). PR-9 (the #3 spine) is correctly
sequenced last: highest blast radius, and its safety net + colliding edits
must land first.
