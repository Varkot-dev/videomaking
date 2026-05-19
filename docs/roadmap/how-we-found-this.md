# Design Spec: Whole-Codebase Analysis → Roadmap → Issues

**Date:** 2026-05-19
**Status:** Approved (pending user spec review)
**Author:** brainstorming session (superpowers methodology)

## Problem

The manimgen pipeline has had three PRs merged this session (bugfixes, Ollama
provider, editor hardening, flaky-test fix). Earlier in the session a 7-agent
constraint-solver analysis surfaced deep structural issues but was scoped to
the constraint/LLM seams and predates the merges. The user wants:

1. A fresh, whole-codebase multi-reviewer sweep.
2. Every CRITICAL/HIGH/MEDIUM finding filed as a GitHub issue.
3. A research pass producing decision-grade pros/cons per direction.
4. An ambitious prioritized 3–4 day PR/feature backlog.
5. A 2-week architecture roadmap document.

## North Star (prioritization lens — applied to every artifact)

Co-equal primary:
- **#3 Velocity & extensibility** — reduce coupling, kill denylist→allowlist
  tech debt, better seams so features are safe to add.
- **#1 Video output quality** — fix the dominant quality bugs (timing/freeze
  tails, content-less cues, layout) — what the viewer sees.

Secondary:
- **#4 Ship-ready / demo-able** — polished end-to-end happy path.

Instrumental (not optimized for, but required): reliability / CI integrity —
a quality fix that can't be trusted to have landed is worthless.

## Approach (chosen: A — specialized review fleet → research fleet)

Two sequential agent waves. Review is parallelized by non-overlapping domain
so there are no gaps and no duplicate work; research is grounded in the real
consolidated findings (not speculation) so cross-cutting tradeoffs can be
reasoned about — which the extensibility+quality goals specifically need.

### Phase 1 — Review fleet (7 agents, parallel, READ-ONLY)

| ID | Domain | Agent type |
|----|--------|-----------|
| R1 | Security: editor, subprocess, LLM keys, generated-code execution, paths | security-reviewer |
| R2 | Python quality & idioms: planner, generator, renderer, cli, utils | python-reviewer |
| R3 | Silent failures & error handling: whole package | silent-failure-hunter |
| R4 | Architecture & constraint-solver seams (the #3 spine) | architect |
| R5 | Test suite & CI integrity: xfail e2e, mocks, contract tests, flakiness | code-reviewer |
| R6 | Editor server (Flask) full review post-hardening | code-reviewer |
| R7 | Pipeline correctness: planner→generator→renderer→validator data-flow | code-explorer |

Every agent: explicit file scope, the North Star lens, "read-only, findings
by severity (CRITICAL/HIGH/MEDIUM/LOW) with file:line", verify against
**current `main`** (post the 3 merges, commit 17cae43).

### Phase 2 — Research fleet (4 agents, parallel, READ-ONLY)

Grounded in Phase-1 consolidated findings. Output format = `tech-decision-table`
style (options × criteria × pros/cons × recommendation), decision-grade for a
learning user, not prose.

- RS1: constraint-solver directions (timing-math, denylist→allowlist, planner
  JSON schema gate, generate-verify-repair).
- RS2: each proposed backlog PR — approaches, libraries, risks, alternatives.
- RS3: test/CI integrity approaches (mocking strategy, xfail e2e repair,
  contract testing, CI hardening).
- RS4: pipeline output-quality approaches (dominant quality bugs, render
  validation, visual QA).

### Phase 3 — Synthesis

- Ambitious prioritized **3–4 day backlog** (menu to pull from): each task =
  scope, exact files, risk, test strategy, effort, dependencies, which known
  bug it addresses, research pros/cons, North-Star tag.
- **2-week architecture roadmap** → `docs/roadmap/2-week-plan.md`: phased
  milestones, the constraint-verification layer, where it sits, migration
  order, what stays LLM vs deterministic.

### Phase 4 — File issues

`gh issue create` for all CRITICAL/HIGH/MEDIUM **directly** (no approval
pause, per user). Batched by subsystem. Labels `severity:*` + `area:*`
(created if absent). One "LOW / nits" tracking issue. Each issue: problem,
evidence (file:line), impact, suggested fix, North-Star tag.

## Components & boundaries

- **Review agents** — input: file scope + lens; output: severity-ranked
  findings with file:line. Independent, no shared state.
- **Consolidation step** (Claude, between phases) — dedupe, rank, reconcile
  vs current `main`. Single source of truth feeding Phase 2 + 4.
- **Research agents** — input: consolidated findings; output: decision tables.
- **Synthesis** (Claude) — produces the two committed docs.
- **Issue filer** (Claude) — deterministic `gh` calls from the consolidated
  finding set.

## Error handling / risks

- Agent overlap → strict non-overlapping domain scoping in prompts.
- Stale findings → every reviewer told to verify vs current `main`.
- Issue spam → MEDIUM+ only individually; LOW batched into one tracking issue.
- Cost → 11 agents total, 2 bounded waves; no open-ended loops.
- This is analysis only — Phases 1–4 write NO production code. The backlog
  feeds future per-PR design→plan→implement cycles (not this spec).

## Testing / verification

This effort produces documents and issues, not code. Verification = the
synthesis cross-checks every filed issue maps to a real file:line, and the
backlog tasks each name a concrete acceptance check. No pipeline code changes.

## Out of scope

- Implementing any backlog item (each gets its own future spec→plan→build).
- The PR #3 timing-math fix (deferred; it becomes the top backlog candidate).
- Refactoring unrelated to the North Star.
