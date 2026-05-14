#!/bin/bash
set -euo pipefail

# audit_gate.sh — Comprehensive video audit CI gate
#
# Runs the full audit pipeline: codeguard → frames → auto-classify → diff
# Exits non-zero (gate FAILS) if any regression detected.
#
# Usage:
#   bash audit_gate.sh --video /path/to/final.mp4 [--out /tmp/manimgen_review] [--prior /tmp/manimgen_review/audit_prior.json]
#
# Environment:
#   Expects manimgen package root at: /Users/varshithkotagiri/Projects/3Blue1Brown/manimgen
#   Output directory defaults to: /tmp/manimgen_review
#

MANIMGEN_ROOT="/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen"
SKILL_TOOLS="$MANIMGEN_ROOT/../.claude/skills/manimgen-video-review/tools"

VIDEO=""
OUT_DIR="/tmp/manimgen_review"
PRIOR_AUDIT=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --video)
            VIDEO="$2"
            shift 2
            ;;
        --out)
            OUT_DIR="$2"
            shift 2
            ;;
        --prior)
            PRIOR_AUDIT="$2"
            shift 2
            ;;
        *)
            echo "Unknown option: $1" >&2
            exit 2
            ;;
    esac
done

if [[ -z "$VIDEO" ]]; then
    echo "error: --video is required" >&2
    exit 2
fi

if [[ ! -f "$VIDEO" ]]; then
    echo "error: video not found: $VIDEO" >&2
    exit 2
fi

mkdir -p "$OUT_DIR"

echo "[audit_gate] Starting full audit pipeline for: $VIDEO"
echo "[audit_gate] Output directory: $OUT_DIR"

# Stage 1: Codeguard pre-check
echo "[audit_gate] Stage 1: Running codeguard audit..."
python3 "$SKILL_TOOLS/codeguard_audit.py" > "$OUT_DIR/codeguard.json" 2>&1
CODEGUARD_WARNINGS=$(python3 -c "import json; print(json.load(open('$OUT_DIR/codeguard.json'))['total_warnings'])")
echo "[audit_gate] Codeguard complete: $CODEGUARD_WARNINGS total warnings"

# Stage 2: Frame extraction
echo "[audit_gate] Stage 2: Extracting section frames..."
python3 "$SKILL_TOOLS/extract_section_frames.py" "$VIDEO" --out "$OUT_DIR"
FRAME_COUNT=$(python3 -c "import json; print(len(json.load(open('$OUT_DIR/frames.json'))['frames']))")
echo "[audit_gate] Extraction complete: $FRAME_COUNT frames extracted"

# Stage 3: Auto-classification
echo "[audit_gate] Stage 3: Running auto-classifier..."
python3 "$SKILL_TOOLS/auto_classify.py" \
    --codeguard "$OUT_DIR/codeguard.json" \
    --frames "$OUT_DIR/frames.json" \
    --video "$VIDEO" \
    --out "$OUT_DIR/audit_auto.json"
AUTO_FAIL=$(python3 -c "import json; print(json.load(open('$OUT_DIR/audit_auto.json'))['auto_fail_count'])")
AUTO_NEEDS_REVIEW=$(python3 -c "import json; print(json.load(open('$OUT_DIR/audit_auto.json'))['needs_review_count'])")
echo "[audit_gate] Auto-classification complete: $AUTO_FAIL auto-FAIL, $AUTO_NEEDS_REVIEW need review"

# Stage 4: Build review brief
echo "[audit_gate] Stage 4: Building review brief..."
python3 "$SKILL_TOOLS/build_review_brief.py" \
    --frames "$OUT_DIR/frames.json" \
    --audit "$OUT_DIR/audit_auto.json" \
    --out "$OUT_DIR/review_brief.md"
echo "[audit_gate] Review brief written to: $OUT_DIR/review_brief.md"

# Stage 5: Regression check (if prior audit exists)
REGRESSION_EXIT=0
if [[ -f "${PRIOR_AUDIT:-}" ]] && [[ -f "$OUT_DIR/audit_auto.json" ]]; then
    echo "[audit_gate] Stage 5: Diffing against prior audit..."
    DIFF_RESULT=$(python3 "$SKILL_TOOLS/diff_audit.py" \
        --current "$OUT_DIR/audit_auto.json" \
        --prior "$PRIOR_AUDIT" 2>&1 || true)
    echo "$DIFF_RESULT" | python3 -m json.tool > "$OUT_DIR/audit_diff.json" 2>&1 || echo "$DIFF_RESULT" > "$OUT_DIR/audit_diff.json"

    REGRESSION_COUNT=$(echo "$DIFF_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('regression_count', 0))" 2>/dev/null || echo "0")
    IMPROVEMENT_COUNT=$(echo "$DIFF_RESULT" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('improvement_count', 0))" 2>/dev/null || echo "0")

    if [[ "$REGRESSION_COUNT" -gt 0 ]]; then
        echo "[audit_gate] ✗ REGRESSION: $REGRESSION_COUNT regressions detected"
        REGRESSION_EXIT=1
    else
        echo "[audit_gate] ✓ No regressions"
    fi
    if [[ "$IMPROVEMENT_COUNT" -gt 0 ]]; then
        echo "[audit_gate] ✓ $IMPROVEMENT_COUNT improvements"
    fi
else
    echo "[audit_gate] Stage 5: Skipping regression check (no prior audit baseline)"
fi

# Final summary
echo ""
echo "[audit_gate] ══════════════════════════════════════"
echo "[audit_gate] AUDIT SUMMARY"
echo "[audit_gate] ══════════════════════════════════════"
echo "[audit_gate] Codeguard warnings:    $CODEGUARD_WARNINGS"
echo "[audit_gate] Frames extracted:      $FRAME_COUNT"
echo "[audit_gate] Auto-FAIL sections:    $AUTO_FAIL"
echo "[audit_gate] Sections need review:  $AUTO_NEEDS_REVIEW"
echo "[audit_gate] Review brief:          $OUT_DIR/review_brief.md"
if [[ -f "$OUT_DIR/audit_diff.json" ]]; then
    echo "[audit_gate] Regression diff:       $OUT_DIR/audit_diff.json (exit code: $REGRESSION_EXIT)"
fi
echo "[audit_gate] ══════════════════════════════════════"
echo ""

if [[ $REGRESSION_EXIT -ne 0 ]]; then
    echo "[audit_gate] GATE FAILED: Regressions detected"
    exit 1
else
    echo "[audit_gate] GATE PASSED"
    exit 0
fi
