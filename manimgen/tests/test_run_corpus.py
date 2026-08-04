"""
Tests for eval/run_corpus.py — the Codeguard resolution-rate harness.

The harness produces a number that is quoted as evidence, so the harness itself
needs to be trustworthy. These tests pin the properties that make the number
meaningful:

  - the scoring criterion is a conjunction (defect cleared AND validation clean),
    so a repair that corrupts the file cannot score as a win;
  - a defect probe that already passes on the unrepaired source is rejected
    outright, so "free wins" cannot silently inflate the rate;
  - every corpus case has a registered probe and honest provenance;
  - the aggregate arithmetic and the source split are correct.

Zero LLM calls, zero subprocess calls.
"""

import json
import os
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from eval.run_corpus import (  # noqa: E402
    _DEFECT_PROBES,
    DEFAULT_CORPUS,
    load_corpus,
    main,
    repair,
    run_case,
    summarize,
)

_VALID_SOURCES = {"git-history", "render-log", "derived-from-fix-rule"}
_VALID_OUTCOMES = {"repairable", "not_repairable", "regression_guard"}


@pytest.fixture(scope="module")
def corpus():
    return load_corpus(DEFAULT_CORPUS)


@pytest.fixture(scope="module")
def results(corpus):
    return [run_case(c) for c in corpus]


# ── corpus integrity ─────────────────────────────────────────────────────────

@pytest.mark.unit
class TestCorpusIntegrity:

    def test_corpus_is_non_empty(self, corpus):
        assert len(corpus) >= 40

    def test_every_case_has_required_sidecar_fields(self, corpus):
        for c in corpus:
            for field in ("case_id", "failure_mode", "error_class",
                          "provenance", "expected_outcome"):
                assert field in c, f"{c.get('case_id')} missing '{field}'"

    def test_every_case_declares_honest_provenance(self, corpus):
        """source/ref/evidence are what separate a real failure from an invented one."""
        for c in corpus:
            prov = c["provenance"]
            assert prov["source"] in _VALID_SOURCES, c["case_id"]
            assert prov["ref"], f"{c['case_id']} has no provenance ref"
            assert len(prov["evidence"]) > 20, (
                f"{c['case_id']} has no substantive evidence string"
            )

    def test_expected_outcomes_are_valid(self, corpus):
        for c in corpus:
            assert c["expected_outcome"] in _VALID_OUTCOMES, c["case_id"]

    def test_case_ids_are_unique(self, corpus):
        ids = [c["case_id"] for c in corpus]
        assert len(ids) == len(set(ids))

    def test_every_failure_mode_has_a_registered_probe(self, corpus):
        """An unregistered mode would silently skip scoring."""
        for c in corpus:
            assert c["failure_mode"] in _DEFECT_PROBES, (
                f"{c['case_id']}: no probe for '{c['failure_mode']}'"
            )

    def test_corpus_retains_real_history_cases(self, corpus):
        """The rate is only credible if some cases are real observed failures."""
        gh = [c for c in corpus if c["provenance"]["source"] == "git-history"]
        assert len(gh) >= 15


# ── scoring semantics ────────────────────────────────────────────────────────

@pytest.mark.unit
class TestScoringSemantics:

    def test_resolution_requires_both_conditions(self, results):
        for r in results:
            assert r["resolved"] == (r["defect_cleared"] and r["validation_clean"])

    def test_corrupted_repair_never_counts_as_resolved(self, results):
        """The #55 class: a 'fix' that leaves the file unparseable is not a win."""
        for r in results:
            if not r["validation_clean"]:
                assert not r["resolved"], r["case_id"]

    def test_unresolved_cases_carry_a_reason(self, results):
        for r in results:
            if not r["resolved"]:
                assert r["unresolved_reason"], r["case_id"]

    def test_free_win_probe_is_rejected(self):
        """A probe passing on unrepaired code cannot measure anything — it must raise."""
        bogus = {
            "case_id": "bogus", "failure_mode": "wrong_import_root",
            "error_class": "ImportError", "expected_outcome": "repairable",
            "provenance": {"source": "derived-from-fix-rule", "ref": "x",
                           "evidence": "y" * 30},
            "stderr": "",
            # already correct: the wrong_import_root probe passes immediately
            "code": "from manimlib import *\n",
        }
        with pytest.raises(AssertionError, match="already passes on the unrepaired"):
            run_case(bogus)

    def test_regression_guard_case_is_exempt_from_free_win_check(self, corpus):
        """gh11 asserts valid code survives untouched — probe passes before AND after."""
        gh11 = next(c for c in corpus if c["case_id"] == "gh11_duplicate_kwarg_repeated")
        assert gh11["expected_outcome"] == "regression_guard"
        out = run_case(gh11)
        assert out["resolved"], "valid VMobject kwargs must survive repair (#55)"
        assert not out["code_changed"], "correct behavior is to change nothing"

    def test_missing_probe_raises(self):
        with pytest.raises(KeyError, match="no defect_probe registered"):
            run_case({
                "case_id": "x", "failure_mode": "__not_a_real_mode__",
                "error_class": "TypeError", "expected_outcome": "repairable",
                "provenance": {"source": "derived-from-fix-rule", "ref": "r",
                               "evidence": "e" * 30},
                "stderr": "", "code": "from manimlib import *\n",
            })


# ── repair path ──────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestRepairPath:

    def test_proactive_path_rewrites_known_defect(self):
        code = "from manim import *\nx = MathTex('a')\n"
        repaired, applied = repair(code, "")
        assert "from manimlib import *" in repaired
        assert "Tex('a')" in repaired
        assert applied

    def test_error_aware_path_runs_only_with_stderr(self):
        code = "from manimlib import *\nnl = NumberLine(tick_size=0.1)\n"
        without, _ = repair(code, "")
        assert "tick_size" in without, "no traceback -> reactive path must not run"
        with_err, applied = repair(
            code,
            "TypeError: NumberLine.__init__() got an unexpected keyword argument "
            "'tick_size'. Did you mean 'tick_frequency'?",
        )
        assert "tick_frequency" in with_err
        assert any("error-aware" in a for a in applied)

    def test_repair_is_pure(self, corpus):
        """Repair must not mutate the caller's source string."""
        for c in corpus[:5]:
            original = c["code"]
            snapshot = str(original)
            repair(original, c.get("stderr", ""))
            assert original == snapshot


# ── aggregation ──────────────────────────────────────────────────────────────

@pytest.mark.unit
class TestSummarize:

    def test_overall_arithmetic(self, results):
        s = summarize(results)
        assert s["overall"]["total"] == len(results)
        assert s["overall"]["resolved"] == sum(r["resolved"] for r in results)
        expected = round(
            100.0 * s["overall"]["resolved"] / s["overall"]["total"], 1
        )
        assert s["overall"]["rate_pct"] == expected

    def test_source_buckets_partition_the_corpus(self, results):
        s = summarize(results)
        assert sum(v["total"] for v in s["by_source"].values()) == len(results)

    def test_reports_history_and_handwritten_separately(self, results):
        """The headline rate must always be decomposable by provenance."""
        s = summarize(results)
        assert "git-history" in s["by_source"]
        assert "derived-from-fix-rule" in s["by_source"]

    def test_unresolved_list_matches_results(self, results):
        s = summarize(results)
        assert len(s["unresolved"]) == sum(not r["resolved"] for r in results)

    def test_rules_fired_counts_are_positive(self, results):
        s = summarize(results)
        assert all(n > 0 for n in s["rules_fired"].values())

    def test_empty_results_do_not_divide_by_zero(self):
        s = summarize([])
        assert s["overall"] == {"total": 0, "resolved": 0, "rate_pct": 0.0}


# ── CLI ──────────────────────────────────────────────────────────────────────

@pytest.mark.integration
class TestCli:

    def test_writes_json_and_markdown(self, tmp_path):
        out = tmp_path / "results"
        rc = main(["--corpus", DEFAULT_CORPUS, "--out", str(out), "--quiet"])
        assert rc == 0

        payload = json.loads((out / "codeguard.json").read_text())
        assert "summary" in payload and "cases" in payload
        assert payload["summary"]["overall"]["total"] == len(payload["cases"])

        md = (out / "codeguard.md").read_text()
        assert "Codeguard resolution rate" in md
        assert "By corpus source" in md

    def test_rate_is_reproducible(self, tmp_path):
        """Same corpus in, same number out — no nondeterminism in the harness."""
        a, b = tmp_path / "a", tmp_path / "b"
        main(["--corpus", DEFAULT_CORPUS, "--out", str(a), "--quiet"])
        main(["--corpus", DEFAULT_CORPUS, "--out", str(b), "--quiet"])
        ja = json.loads((a / "codeguard.json").read_text())["summary"]["overall"]
        jb = json.loads((b / "codeguard.json").read_text())["summary"]["overall"]
        assert ja == jb

    def test_missing_corpus_dir_raises(self, tmp_path):
        with pytest.raises((FileNotFoundError, ValueError)):
            main(["--corpus", str(tmp_path / "nope"), "--out", str(tmp_path)])
