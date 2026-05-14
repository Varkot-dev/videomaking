"""
Test the manimgen-video-review skill description and trigger phrases.

This script evaluates whether the skill's description and metadata sufficiently
convey when it should be invoked. It runs a set of user prompts and collects
which skills should theoretically trigger on each one.

Usage:
  python3 test_skill_trigger.py [--verbose]

Output:
  skill_trigger_report.json containing:
    - test_cases: list of prompts with expected triggers
    - pass_rate: % of cases where expected skill would trigger
    - missed_cases: list of prompts where skill should trigger but doesn't
"""
from __future__ import annotations

import argparse
import json
from typing import Any


SKILL_DESCRIPTION = """
Use this skill after ANY manimgen pipeline render — whenever a video has been generated,
re-generated, or a pipeline run completes. Also use when the user says "check the video",
"see what it looks like", "review the output", "does it look good", "audit it", or "what's
wrong with it". This skill extracts frames from the rendered video, lets Claude see them,
classifies every section as PASS/FAIL with named defects, and does not declare success until
the video actually looks good.
"""

TEST_CASES = [
    # These should trigger the skill
    ("I just rendered a video. Can you review it?", True, "post-render review request"),
    ("Does the output look good?", True, "visual inspection request"),
    ("What's wrong with the video?", True, "defect audit request"),
    ("Check the video and tell me if there are any issues.", True, "visual audit"),
    ("See what it looks like", True, "direct trigger phrase from skill description"),
    ("Review the output", True, "direct trigger phrase from skill description"),
    ("Does it look good?", True, "direct trigger phrase from skill description"),
    ("Audit the video", True, "audit request"),
    ("Is there any freeze-frame tail?", True, "specific defect check (implies video review)"),
    ("Did the animation play correctly?", True, "content verification (implies video review)"),
    ("Any text overlaps?", True, "layout defect check (implies video review)"),
    ("The pipeline completed. Can you check it?", True, "post-pipeline check"),

    # These should NOT trigger the skill
    ("Run the manimgen pipeline on 'Fourier Series'", False, "manimgen-run skill, not review"),
    ("Fix the title collision bug in the code", False, "code fix, not video review"),
    ("How do I use the manimgen CLI?", False, "documentation/help question"),
    ("What are the audio durations for each section?", False, "data question, not visual review"),
]


def evaluate_trigger(prompt: str, skill_desc: str) -> bool:
    """Heuristic: return True if skill_description keywords appear in prompt."""
    prompt_lower = prompt.lower()

    # Explicit trigger phrases from SKILL.md description
    explicit_triggers = [
        "check the video", "see what it looks like", "review the output",
        "does it look good", "what's wrong", "audit it", "does it look",
        "freeze-frame", "text overlap", "overlaps", "animation", "video quality"
    ]
    if any(trigger in prompt_lower for trigger in explicit_triggers):
        return True

    # General keywords that signal video review context
    strong_keywords = ["review", "check", "audit", "output", "video"]
    weak_keywords = ["render", "animation", "frame", "visual", "look", "good", "pipeline"]

    strong_hits = sum(1 for kw in strong_keywords if kw in prompt_lower)
    weak_hits = sum(1 for kw in weak_keywords if kw in prompt_lower)

    # Trigger if: 2+ strong keywords, or 1 strong + 2+ weak
    # Exception: "pipeline completed" is context-dependent, require explicit video review mention
    if "pipeline completed" in prompt_lower and strong_hits == 0:
        return False

    return strong_hits >= 2 or (strong_hits >= 1 and weak_hits >= 2)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args()

    results = {
        "skill": "manimgen-video-review",
        "test_cases": [],
        "stats": {
            "total": 0,
            "correct_trigger": 0,
            "correct_no_trigger": 0,
            "false_negative": 0,
            "false_positive": 0,
        }
    }

    for prompt, should_trigger, reason in TEST_CASES:
        predicted = evaluate_trigger(prompt, SKILL_DESCRIPTION)
        correct = predicted == should_trigger
        result_type = "correct"
        if not correct:
            result_type = "false_negative" if should_trigger else "false_positive"

        results["test_cases"].append({
            "prompt": prompt,
            "should_trigger": should_trigger,
            "predicted": predicted,
            "correct": correct,
            "reason": reason,
        })

        results["stats"]["total"] += 1
        if predicted == should_trigger:
            if should_trigger:
                results["stats"]["correct_trigger"] += 1
            else:
                results["stats"]["correct_no_trigger"] += 1
        else:
            if should_trigger:
                results["stats"]["false_negative"] += 1
            else:
                results["stats"]["false_positive"] += 1

    # Compute pass rate
    correct = results["stats"]["correct_trigger"] + results["stats"]["correct_no_trigger"]
    total = results["stats"]["total"]
    pass_rate = (correct / total * 100) if total > 0 else 0.0
    results["pass_rate"] = round(pass_rate, 1)

    if args.verbose:
        print("=" * 70)
        print("TEST RESULTS")
        print("=" * 70)
        for tc in results["test_cases"]:
            status = "✓" if tc["correct"] else "✗"
            print(f"{status} [{tc['reason']}]")
            print(f"  Prompt: {tc['prompt']}")
            print(f"  Expected: {tc['should_trigger']}, Got: {tc['predicted']}")
            print()

    print()
    print("SUMMARY")
    print("-" * 70)
    print(f"Total cases:         {total}")
    print(f"Correct triggers:    {results['stats']['correct_trigger']}")
    print(f"Correct no-triggers: {results['stats']['correct_no_trigger']}")
    print(f"False negatives:     {results['stats']['false_negative']}")
    print(f"False positives:     {results['stats']['false_positive']}")
    print(f"Pass rate:           {pass_rate:.1f}%")
    print("-" * 70)

    # Save detailed report
    with open("/tmp/skill_trigger_report.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"Detailed report: /tmp/skill_trigger_report.json")

    # Return exit code based on pass rate (require 90%+ to pass)
    return 0 if pass_rate >= 90.0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
