# Branch Protection — required setup

The CI workflow (`.github/workflows/ci.yml`) only *enforces* anything if `main`
is protected to require it. The workflow file alone does **not** block merges —
that is a repository setting you must apply once, by hand, in GitHub.

## Why this is a manual step

Branch protection rules live in GitHub repo settings, not in the repo tree.
Nothing in this PR can set them; an admin must.

## Apply this (Settings → Branches → Add branch ruleset / protection rule)

Target branch: `main`

Enable:

1. **Require a pull request before merging**
   - Require at least 1 approving review
   - Dismiss stale approvals when new commits are pushed
2. **Require status checks to pass before merging**
   - Require branches to be up to date before merging
   - Required checks (exact job names from `ci.yml`):
     - `Lint (ruff)`
     - `Full test suite (no ignores)`
     - `Mocked-LLM pipeline smoke`
3. **Do not allow bypassing the above settings** (apply to admins too —
   otherwise the gate is advisory, which defeats the purpose)
4. Optional but recommended: **Require conversation resolution before merging**

## Verifying it works

After enabling, open any PR with a deliberately failing test. The merge button
must be blocked until CI is green. If it can still be merged, the required
checks are not wired to the exact job names above — re-check step 2.

## Scope note (intentional, tracked)

- The lint/format gate covers `manimgen/` source only. `tests/` (~27
  unformatted files, ~200 lint issues, never linted) is **deliberately out of
  scope** for now because cleaning it would collide with in-flight PRs. It is
  tracked as separate follow-up work — not an oversight.
- `tests/test_pipeline_e2e.py::test_full_pipeline_success` is
  `xfail(strict=True)` with the remaining un-stale work documented in the test.
  `--strict-markers` in CI means if someone fixes it without removing the
  xfail, CI fails loudly (good — forces the marker to be cleaned up).
