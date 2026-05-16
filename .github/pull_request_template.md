## Summary

<!-- What does this PR change and why? 1-3 sentences. -->

## Changes

<!-- Bullet the concrete changes. -->
-

## Test plan

<!-- Every box must be checked or explicitly justified before merge. -->
- [ ] `ruff check manimgen/` passes locally
- [ ] `ruff format --check manimgen/` passes locally
- [ ] Full suite green locally: `python3 -m pytest tests/ -q` (no `--ignore` flags)
- [ ] If pipeline behavior changed: validated by an actual render OR the mocked smoke tests, and the result is described above (unit tests do NOT prove a render looks right)
- [ ] No new `--ignore` / skipped tests introduced to make CI pass (use `xfail(strict=True)` with a documented reason + tracking note instead)
- [ ] Touched code stays inside the strict-lint scope (`manimgen/`); any `ruff.toml` allowlist entry added is documented and justified

## Risk / blast radius

<!-- What could this break? Does it touch a file another open PR also changes?
     Note any branch-collision risk explicitly. -->

## Out of scope / follow-ups

<!-- Pre-existing issues you deliberately did NOT fix here, with a pointer. -->
