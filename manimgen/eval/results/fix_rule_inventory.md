# Codeguard fix-rule inventory and mechanism audit

Source of truth: `manimgen/validator/codeguard.py` (1,600 lines at time of audit).
Counts below were produced by AST-walking the module, not by eyeballing it.

## 1. Distinct fix rules — exact count

A "rule" is one distinct defect the module knows how to act on: a single entry in
a rule table, one inline rewrite, or one dedicated fixer/checker function.

### Rules that REWRITE code (auto-fixes)

| # | Category | Where | Rules |
|---|---|---|---|
| 1 | ManimCommunity→ManimGL symbol/method/kwarg rewrites | `apply_known_fixes` → `replacements` | **19** |
| 2 | Palette hex → ManimGL constant | `apply_known_fixes` → `_HEX_TO_CONSTANT` | **11** |
| 3 | Banned kwarg strips | `_BANNED_KWARGS` | **7** |
| 4 | Inline ad-hoc rewrites (multi-arg fades, zero-length Arrow, frame bounds, wait clamp, …) | `apply_known_fixes` body | **10** |
| 5 | Dedicated proactive fixer functions | module-level `_fix_*` / `_strip_*` / `_remove_*` / `_inject_*` / `_wrap_*` (excludes the `_snap_to_canonical_font_size` helper) | **15** |
| 6 | Kwarg-normalization registry entries | `_KWARG_NORMALIZATION_REGISTRY` | **7** |
| 7 | Color-role header injections | `_COLOR_ROLE_CONSTANTS` | **8** |
| 8 | Error-aware name remaps | `apply_error_aware_fixes` → `_name_fixes` | **16** |
| 9 | Error-aware inline rewrites (latex-missing, broadcast, `.animate` split, …) | `apply_error_aware_fixes` body | **7** |
| | **Total rewriting rules** | | **100** |

### Rules that only DETECT (block or warn, never rewrite)

| # | Category | Where | Rules |
|---|---|---|---|
| 10 | Banned-pattern denylist (blocks render) | `_BANNED_PATTERNS` | **24** |
| 11 | Layout/timing smell checkers | `_check_*` / `_detect_*` functions | **8** |
| | **Total detection rules** | | **32** |

### Headline number

- **100 distinct auto-fix rules** that rewrite code.
- **32 additional detection-only rules**.
- **132 rules total.**

Counting only the dedicated fixer *functions* (the most conservative possible
reading, ignoring every table entry) still yields **15** — already below what a
"20+" claim implies, while every broader reading is 5–6× above it.

The prior audit's "roughly 60+" was a reasonable order-of-magnitude estimate;
the precise figure is higher because table entries (19 + 11 + 16 + 8 …) are
individually distinct rules.

## 2. Mechanism: regex vs AST — exact count

Counted as attribute accesses on the `re` and `ast` modules within
`codeguard.py`:

| Module | Calls | Breakdown |
|---|---|---|
| `re.*` | **113** | `subn` 30, `compile` 29, `search` 27, `escape` 7, `Match` 5, `findall` 4, `match` 3, `sub` 2, `DOTALL` 2, `finditer` 1, `VERBOSE` 1, other 2 |
| `ast.*` | **6** | `parse` 2, `walk` 1, `Name` 1, `Load` 1, `Store` 1 |

Ratio: **≈19:1 in favour of regex.**

### Where AST is actually used

All 6 `ast.*` references serve exactly two purposes:

1. `validate_scene_code` calls `ast.parse(code)` once as a **syntax check** — it
   parses to detect `SyntaxError` and discards the tree. That is validation, not
   AST-based transformation.
2. `_inject_color_role_header` genuinely walks the AST (`ast.walk`, `ast.Name`,
   `ast.Load`, `ast.Store`) to distinguish a real identifier use from the word
   appearing in a comment or string — added in `9b9db79` to fix #56.

So **exactly one** of the 100 rewriting rules is AST-based. Every other repair is
a regular-expression substitution over raw source text.

### Verdict on the mechanism word

**"AST-based" is not accurate.** The mechanism is **regex-first**: 113 regex
operations against 6 AST references, with a single AST-driven fix rule. An
accurate description is *"regex-based source rewrites with an AST syntax gate
and one AST-based rule."*

This is not merely a wording quibble — it is the documented cause of real
production failures. Because the rewrites are context-free string substitutions:

- the `fill_color`/`fill_opacity` rewrite corrupted valid `VMobject` code into
  duplicate kwargs → `SyntaxError: keyword argument repeated` (#55), forcing
  fallback cards on sections 1/2/3 of every binary-search render, until it was
  deleted in `9b9db79`;
- the `_BANNED_KWARGS` strip regex `[^,\)\n]+` still cannot span a list literal,
  so `checkerboard_colors=[BLUE_D, BLUE_E]` is truncated mid-list, leaving
  `v_range=[-2, 2], BLUE_E])` — reproduced by corpus case `gh04`, and the same
  corruption class the root-cause doc recorded as "the retry loop's own
  auto-fixer corrupting the file".

A real AST-based implementation could not produce either defect.
