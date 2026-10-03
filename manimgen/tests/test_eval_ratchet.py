"""Ratchet for Codeguard: it may repair more and damage less, never the reverse.

`eval/results/baseline.json` holds the numbers measured from the code at the time
it was committed. These tests rerun the harness (no network, no LLM, seconds) and
fail when:

  - the static repair count drops for any corpus source, or
  - an example in `examples/` that used to survive Codeguard no longer does, or
  - total damage to known-good examples rises, or any one example gets worse.

When a Codeguard change improves a number, run
`python3 eval/run_corpus.py --write-baseline` and commit the new baseline.
Never loosen the baseline to make a damaging change pass.
"""

import json
import re
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from eval.damage import (  # noqa: E402
    DEFAULT_EXAMPLES,
    measure_damage,
    run_examples,
    summarize_examples,
)
from eval.run_corpus import (  # noqa: E402
    BASELINE_PATH,
    DEFAULT_CORPUS,
    load_corpus,
    run_case,
    summarize,
)
from manimgen.validator.codeguard import precheck_and_autofix  # noqa: E402

_HINT = " (improvement? run `python3 eval/run_corpus.py --write-baseline`)"


@pytest.fixture(scope="module")
def baseline():
    return json.loads(Path(BASELINE_PATH).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def example_rows():
    return run_examples()


# ── the ratchet ──────────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRatchet:
    def test_repair_count_does_not_drop(self, baseline):
        summary = summarize([run_case(c) for c in load_corpus(DEFAULT_CORPUS)])
        for source, floor in baseline["repair_resolved_by_source"].items():
            now = summary["by_source"].get(source, {}).get("resolved", 0)
            assert now >= floor, f"{source}: resolved {now} < baseline {floor}"
        assert summary["overall"]["resolved"] >= baseline["repair_resolved_total"]

    def test_intact_example_count_does_not_drop(self, baseline, example_rows):
        now = summarize_examples(example_rows)["passed"]
        assert now >= baseline["examples"]["passed"], "examples intact dropped"

    def test_no_previously_intact_example_breaks(self, baseline, example_rows):
        broken = [
            r["example"]
            for r in example_rows
            if baseline["example_passes"].get(r["example"]) and not r["passes"]
        ]
        assert not broken, f"examples Codeguard now breaks: {broken}"

    def test_total_damage_does_not_rise(self, baseline, example_rows):
        now = summarize_examples(example_rows)["total_damage"]
        floor = baseline["examples"]["total_damage"]
        assert now <= floor, f"damage {now} > baseline {floor}"

    def test_no_example_gets_worse(self, baseline, example_rows):
        worse = {
            r["example"]: (baseline["example_damage"][r["example"]], r["damage"])
            for r in example_rows
            if r["example"] in baseline["example_damage"]
            and r["damage"] > baseline["example_damage"][r["example"]]
        }
        assert not worse, f"per-example damage rose (baseline, now): {worse}"

    def test_new_examples_must_be_in_baseline(self, baseline, example_rows):
        missing = [
            r["example"]
            for r in example_rows
            if r["example"] not in baseline["example_damage"]
        ]
        assert not missing, f"examples absent from baseline.json: {missing}" + _HINT

    def test_every_example_still_compiles(self, example_rows):
        assert [r["example"] for r in example_rows if not r["compiles"]] == []


# ── the ratchet must actually go red on damage ───────────────────────────────


def _damage_total(repair_fn):
    return summarize_examples(run_examples(repair_fn=repair_fn))["total_damage"]


@pytest.mark.unit
class TestRatchetDetectsDamage:
    """Simulate damaging rules in-process and show the metric reacts."""

    def test_rename_to_nonexistent_class_is_flagged(self, baseline):
        def bad(code):
            return precheck_and_autofix(code).replace("ShowCreation(", "Create(")

        rows = run_examples(repair_fn=bad)
        assert any("Create" in r["new_undefined"] for r in rows)
        assert summarize_examples(rows)["total_damage"] > baseline["examples"][
            "total_damage"
        ]

    def test_dropping_an_animation_is_flagged(self, baseline):
        def bad(code):
            return re.sub(r"^\s*self\.wait\([^)]*\)\n", "", code, flags=re.M)

        rows = run_examples(repair_fn=bad)
        assert any(not r["play_wait_kept"] for r in rows)
        assert summarize_examples(rows)["passed"] < baseline["examples"]["passed"]

    def test_syntax_error_is_flagged(self, baseline):
        rows = run_examples(repair_fn=lambda c: precheck_and_autofix(c) + "\n(")
        assert all(not r["compiles"] for r in rows)
        assert summarize_examples(rows)["total_damage"] > baseline["examples"][
            "total_damage"
        ]

    def test_identity_repair_has_zero_damage(self):
        assert _damage_total(lambda c: c) == 0


@pytest.mark.unit
class TestMeasureDamage:
    SRC = "from manimlib import *\nclass A(Scene):\n    def construct(self):\n        self.play(FadeIn(Circle()))\n        self.wait(1)\n"

    def test_unchanged_source_is_undamaged(self):
        r = measure_damage(self.SRC, self.SRC)
        assert r["passes"] and r["damage"] == 0 and not r["changed"]

    def test_removed_class_fails(self):
        r = measure_damage(self.SRC, "from manimlib import *\n")
        assert not r["defs_kept"] and not r["passes"]

    def test_font_size_snap_is_counted_but_still_passes(self):
        before = self.SRC.replace(
            "        self.play(", "        t = Text('a', font_size=26)\n        self.play("
        )
        after = before.replace("26", "28")
        r = measure_damage(before, after)
        assert r["passes"] and r["damage"] == 1

    def test_broken_input_is_not_scored(self):
        assert measure_damage("def (", "def (")["input_compiles"] is False


@pytest.mark.unit
def test_examples_dir_is_non_empty():
    assert list(Path(DEFAULT_EXAMPLES).glob("*.py"))
