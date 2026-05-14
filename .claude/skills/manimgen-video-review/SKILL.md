---
name: manimgen-video-review
description: >
  Use this skill after ANY manimgen pipeline render — whenever a video has been generated,
  re-generated, or a pipeline run completes. Also use when the user says "check the video",
  "see what it looks like", "review the output", "does it look good", "audit it", or "what's
  wrong with it". This skill extracts frames from the rendered video, lets Claude see them,
  classifies every section as PASS/FAIL with named defects, and does not declare success until
  the video actually looks good.
---

# ManimGen Video Review

You have vision. Use it. Never trust a pipeline run just because it exited 0.

## When to use this skill

After every render. Before telling the user anything is "done" or "fixed". If a section fell
back to bullet points, if there are freeze-frame tails, if labels overlap — you need to see
it yourself rather than guess from logs.

## The audit pipeline (run scripts in this order)

The four `tools/` scripts implement a layered audit: cheap deterministic checks first, then
expensive vision checks, then structured output, then regression diff against prior runs.

```bash
SKILL=/Users/varshithkotagiri/Projects/3Blue1Brown/.claude/skills/manimgen-video-review/tools
VIDEO=<path/to/final.mp4>
OUT=/tmp/manimgen_review

# 1. Cheap codeguard pre-check — emits warnings about source code defects
#    you'd otherwise have to spot in pixels. Run BEFORE looking at frames.
python3 $SKILL/codeguard_audit.py > $OUT/codeguard.json

# 2. Frame extraction — open/mid/close per section, plus intro/outro bookends
python3 $SKILL/extract_section_frames.py "$VIDEO" --out $OUT
# → writes frame_*.jpg files and $OUT/frames.json describing each one

# 3. Read each frame with the Read tool, apply the binary checklist below,
#    then write the result:
python3 $SKILL/write_audit.py --out $OUT/audit.json --video "$VIDEO" \
    --section '1:PASS' \
    --section '2:FAIL:title-zone collision: scene_title and geo_title at top edge' \
    --codeguard-warnings-total <total>

# 4. Regression diff — only if a prior audit exists
python3 $SKILL/diff_audit.py --current $OUT/audit.json --prior $OUT/audit_prior.json
```

Always start with codeguard.json — it tells you which sections to inspect most carefully.
Sections with codeguard warnings are FAIL by default unless you can prove otherwise from frames.

## Step 1: Codeguard pre-check (zero cost)

```bash
python3 $SKILL/codeguard_audit.py
```

This runs `_check_layout_smells` against every `output/scenes/section_*.py`. Each warning
names a real defect that's already in the code. No need to extract frames to confirm.

If a section has a `title zone (UP edge / UR-UL corner)` warning, that section is FAIL on
layout grounds before you look at any pixels. Note it in the audit and only check frames
to confirm the visual manifestation.

## Step 2: Extract frames at section boundaries

```bash
python3 $SKILL/extract_section_frames.py "$VIDEO" --out $OUT
```

The script reads `plan.json` and the muxed clip durations to compute per-section start times,
then extracts:

- **opening (open):** start + 0.5s and start + 2s — catches title-zone collisions
- **midpoint (mid):** section center — catches the main content
- **closing (close):** end - 0.5s — catches uncleaned residual mobjects, missing FadeOut
- **bookends (intro/outro):** first 2s and last 2s of the full video

`frames.json` maps each frame back to (section_id, section_title, phase). For a 6-section
2-min video, expect ~24 frames.

## Step 3: Read every frame with the Read tool

Don't skim. Look at each one and apply the binary checks below.

## Step 4: Run the binary checklist per frame

Each item is **pass or fail** — no "minor" or "partial". If the user can see it, it counts.
For each frame, ask each question. Any failure ⇒ that section is FAIL.

### A. Title-zone exclusivity (the most common bug)

- Are there 2+ pieces of text in the top band (y > 2.5, top ~25% of frame)?
- Do they visibly overlap or sit at the same y-coordinate creating crowded text?
- Does a section title spanning the full width cross into where panel titles or
  corner readouts sit?

If yes: **FAIL — title-zone collision.** Name the colliding text mobjects exactly.

### B. Text-on-text overlap

- Look at every text element. Is any letter or word from one mobject visually
  rendering on top of another mobject's letter or word?
- Are there double-edges, ghost characters, or letters that look "doubled" (a sign
  of two text mobjects rendering at the same coordinate)?

If yes: **FAIL — text overlap.** Quote the overlapping strings.

### C. Edge clipping

- Is any element cut off at the screen edge (text whose final character is missing,
  arrows whose tips run off the canvas, axes labels at -6 or 6 that get truncated)?

If yes: **FAIL — edge clipping.** Name the clipped element.

### D. Fallback / frozen scene

- Does the frame contain only a section number, section title, and a one-line
  description (the fallback title-card pattern)?
- Are 3 frames sampled across the section visually identical to each other?

If yes: **FAIL — fallback** (or **FAIL — full-section freeze**).

### E. Layout proportions

- Do labels sit on top of axes lines or tick numbers?
- Are 3+ elements crowded into the same ~1 unit area with no spacing?
- Does the scene have inappropriate dead space (e.g. content in a 30% strip with
  70% empty)?

If yes: **FAIL — proportion** (or **layout density**).

### F. A/V sync (audio-video synchronization)

- Did ffmpeg report stream duration mismatches during muxing?
- Are there codec parameter conflicts (sample rate, channels, format)?
- Do frames arrive out of order (DTS violations)?
- Is any stream truncated during mux?

Check the muxer log (saved during pipeline run) for warnings. If yes: **FAIL — av_sync**.

## Step 5: Classify each section as PASS / FAIL — never PARTIAL

Build a section-by-section table. Each section is either PASS (no defects from steps A–E
across all sampled frames) or FAIL (one or more defects). If a section has good content but
also has one named defect, it is FAIL — not PARTIAL. The previous version of this skill
used PARTIAL as a hedge and consistently let title-overlap bugs ship.

Format (use exactly this — it makes regressions easy to compare):

```
SECTION 1 (0–16s, "Section Title") — PASS
SECTION 2 (16–39s, "Section Title") — FAIL
  [0:18] title-zone collision: scene_title and geo_title both at top edge
  [0:36] edge clipping: "Effective Force" label cut off at right
SECTION 3 (39–62s, "Section Title") — FAIL — fallback
```

## Step 6: Write structured audit JSON

Once you've classified every section, persist the result so future runs can diff against it:

```bash
python3 $SKILL/write_audit.py \
  --out $OUT/audit.json \
  --video "$VIDEO" \
  --section '1:PASS' \
  --section '2:FAIL:title-zone collision: scene_title and geo_title at top edge' \
  --codeguard-warnings-total <total>
```

Defect categories accepted:
`title_zone_collision`, `text_overlap`, `edge_clipping`, `fallback`, `freeze`,
`layout_proportion`, `codeguard`.

The detail string is parsed for category keywords, so phrasing like "title zone..."
auto-classifies as `title_zone_collision`.

## Step 7: Diff against prior audit (regression check)

If a baseline exists for this topic, diff to surface regressions and improvements.
Use the baseline manager to fetch the prior audit:

```bash
TOPIC="Fourier Series"  # or extract from plan.json
python3 $SKILL/manage_baselines.py --get --topic "$TOPIC" --base $OUT/baselines > $OUT/audit_prior.json

if [[ -f $OUT/audit_prior.json ]]; then
  python3 $SKILL/diff_audit.py --current $OUT/audit.json --prior $OUT/audit_prior.json
fi
```

Exit code is 1 if any regressions, 0 if all-clean. Examples of what gets reported:
- `section 6 PASS → FAIL` (status regressed)
- `section 6: new defect category 'edge_clipping'` (new defect type)
- `codeguard warnings increased: 5 → 9`

After a successful run, store the current audit as the new baseline:
```bash
python3 $SKILL/manage_baselines.py --set --topic "$TOPIC" --audit $OUT/audit.json --base $OUT/baselines
```

Or use the all-in-one CI gate script which handles baseline lookup and storage:

```bash
bash $SKILL/audit_gate.sh --video /path/to/final.mp4 --prior-topic "Fourier Series" --baseline-dir /tmp/manimgen_review/baselines
```

## Step 8: Show evidence

Pick the single best-looking frame as proof of working pipeline. Pick the single worst frame
as evidence of each FAIL. Reference frame paths so the user can re-check (e.g.
`/tmp/manimgen_review/frame_72s.jpg`).

## Step 9: Decide what to fix

| Defect | Where the fix belongs |
|---|---|
| title-zone collision (A) | `_check_top_edge_collision` in `codeguard.py` should have caught — verify it ran; also Director prompt visual-continuity rule |
| text-on-text overlap (B) | Director prompt or codeguard layout-smell |
| edge clipping (C) | Director prompt safe-bounds rule |
| fallback / freeze (D) | Pipeline retry/timing fixes (see `manimgen-test-fix`) |
| layout proportion (E) | Director prompt archetype rules |

If half or more of the sections FAIL with the same defect type, that's a systemic Director
prompt or codeguard issue — fix it once at the source rather than per-section.

## Step 10: Re-render only when fixes are in code, not in narration

If you've changed the Director prompt or codeguard, clear the cached renders for the affected
sections and re-run with `--resume`. If you've only "told the user" how it should look, you
have not fixed anything.

```bash
rm /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/videos/Section*.mp4
rm /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/videos/Section*.hash
GEMINI_API_KEY=$(grep GEMINI_API_KEY .env | cut -d= -f2) python3 -m manimgen.cli "<topic>" --resume
```

## What good looks like

A passing video has:
- Dark (#1C1C1C) background throughout
- Animations actually playing (no full-section freeze)
- One mobject in the title zone at any given time — never two
- Text that doesn't overlap other text
- Audio and video roughly in sync (no 5+ second freeze-frame tails)
- No fallback title cards

Show the user at least one frame from a PASS section as proof.
