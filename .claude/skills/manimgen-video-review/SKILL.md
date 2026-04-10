---
name: manimgen-video-review
description: >
  Use this skill after ANY manimgen pipeline render — whenever a video has been generated,
  re-generated, or a pipeline run completes. Also use when the user says "check the video",
  "see what it looks like", "review the output", "does it look good", or "what's wrong with it".
  This skill extracts frames from the rendered video, lets Claude see them directly, identifies
  every visual defect with timestamps, and does not declare success until the video actually looks good.
---

# ManimGen Video Review

You have vision. Use it. Never trust a pipeline run just because it exited 0.

## When to use this skill

After every render. Before telling the user anything is "done" or "fixed". If a section fell back to bullet points, if there are freeze-frame tails, if labels overlap — you need to see it yourself rather than guess from logs.

## Step 1: Extract frames

Use ffmpeg to pull frames at multiple points in the video. Always extract:
- First 2 seconds (one frame at 0.5s, one at 1.5s) — catches opening layout
- 25%, 50%, 75% of duration — catches mid-scene issues  
- Last 2 seconds — catches freeze-frame tails and fallback bullet points

```bash
# Get video duration first
ffprobe -v quiet -show_entries format=duration -of csv=p=0 <video_path>

# Extract frames (replace TIMESTAMP with actual values)
ffmpeg -ss TIMESTAMP -i <video_path> -vframes 1 -q:v 2 /tmp/manimgen_review/frame_TIMESTAMP.jpg -y
```

Always save to `/tmp/manimgen_review/` and create that directory first with `mkdir -p /tmp/manimgen_review`.

Extract at least 6 frames per video. For a 3-minute video extract every 15 seconds.

## Step 2: Read every frame

Use the Read tool on each extracted frame. Actually look at it. Don't skim.

For each frame, check:

**Layout problems:**
- Is the title overlapping a LaTeX equation? (Both at top of screen)
- Are y-axis numbers rotated 90° and stacked on top of each other?
- Are labels piling up at the same position?
- Is any text cut off at screen edges?
- Is anything outside the visible frame area?

**Content problems:**
- Is this a bullet point fallback? (Look for plain text bullets instead of animations)
- Is the scene completely black or frozen?
- Is the same static frame repeating across multiple timestamps? (freeze-frame tail)
- Are there 3+ elements crowded into one area with no spacing?

**Quality problems:**
- Does it look like a 3Blue1Brown video or like a broken slide deck?
- Is the dark background (#1C1C1C) present?
- Are the animations actually visible or is it just text on black?

## Step 3: Compare frames for freeze-frame detection

If the frame at 50% looks identical to the frame at 75% and 90%, that's a freeze-frame tail. 
Report: "Section X has a freeze-frame tail starting at Ys — the last Z seconds are frozen."

## Step 4: Report specific issues

Don't say "there are some issues". Be specific:

```
SECTION 4 (gradient_descent):
- [0:23] Y-axis numbers are rotated and stacked — looks unreadable
- [0:31-0:45] FREEZE-FRAME TAIL — 14 seconds of frozen image while audio plays
- [1:02] Title "The Update Rule" overlaps LaTeX equation θ_new = ...

SECTION 3 (mathematizing):
- FALLBACK — entire section rendered as bullet points, no animations

SECTION 1, 2, 5: PASS — animations playing, no obvious layout issues
```

## Step 5: Decide what to fix

If any section is a bullet point fallback → that's a pipeline failure, needs a re-render with better scene code.

If freeze-frame tails > 3 seconds on more than half the cues → the timing bug is systemic, need to fix the Director prompt and re-render.

If layout overlaps exist → codeguard should catch them. Check if the fix worked.

If everything looks good → say so clearly and show the user the best-looking frame as evidence.

## Step 6: Fix and re-render if needed

Don't just report issues — fix them. After identifying the root cause from visual inspection:

1. If it's a codeguard-catchable issue (y-axis numbers, title overlap): add the fix to codeguard, clear cache, re-render
2. If it's a timing issue: check the generated scene file, find the wrong `self.wait()` calculation, fix it directly in the scene file, re-render that section only
3. If it's a fallback: look at the error log for that section, understand why it failed, fix the underlying cause

To re-render a single section without re-running the whole pipeline:
```bash
# Find the scene file
ls manimgen/output/scenes/section_0X.py

# Delete just that section's muxed clips to force re-render
rm manimgen/output/muxed/section_0X_*.mp4

# Re-run pipeline with --resume flag (uses cached plan and audio)
export $(cat .env | xargs) && python3 -m manimgen.cli "TOPIC" --resume
```

## What good looks like

A passing video has:
- Dark (#1C1C1C) background throughout
- Animations actually playing (not static)  
- Text that doesn't overlap other text
- Audio and video roughly in sync (no 5+ second freeze-frames)
- No bullet point fallbacks

Show the user at least one frame from a passing section as proof.
