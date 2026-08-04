"""Measure Codeguard's real static-repair resolution rate over eval/corpus.

Runs Codeguard's actual static repair path over every labelled corpus case and
records, per case, whether the specific defect was removed.

Why not just "does it pass validate_scene_code() after repair?"
---------------------------------------------------------------
Because that question is far too easy, and would badly inflate the number.
`validate_scene_code` only checks (a) Python syntax, (b) the `_BANNED_PATTERNS`
denylist, and (c) the design-system invariants. It has no idea that `x_length=`,
`DARK_GREY`, or `.get_graph_point(` are wrong — those are real ManimGL crashes
that a static AST/syntax check simply cannot see. 22 of the 57 corpus cases pass
`validate_scene_code` *before any repair runs at all*. Scoring on that criterion
alone would hand Codeguard 22 free wins for defects it never touched.

So resolution is scored on a conjunction, per case:

  1. `validate_scene_code(repaired)` is clean  — the repaired code must still be
     syntactically valid and free of blocking banned patterns. This is
     Codeguard's own AST/syntax validation, and it is a necessary condition: it
     is what catches a repair that *corrupts* the file (the real #55 regression).
  2. The case's defect marker is gone from the repaired source — a per-case
     `defect_probe` (see `_DEFECT_PROBES`) asserting the specific broken
     construct named in the sidecar is no longer present.

Both must hold. (1) alone is too weak; (2) alone would miss self-inflicted
corruption. `not_repairable` cases are still run and scored identically — they
are not excluded, so the headline rate reflects the whole corpus, including the
defects Codeguard is documented as unable to fix.

Entry points exercised (the real ones, read from the source):
  - `precheck_and_autofix`   — the proactive path, called by scene_generator
                               before saving and by retry via the _file variant.
  - `apply_error_aware_fixes` — the reactive path, called by retry.py with the
                               render traceback, only for cases whose sidecar
                               carries a `stderr`.

Usage:
    python3 eval/run_corpus.py                     # writes eval/results/*
    python3 eval/run_corpus.py --corpus DIR --out DIR
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from typing import Any, Callable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from manimgen.validator.codeguard import (  # noqa: E402
    apply_error_aware_fixes,
    apply_known_fixes,
    precheck_and_autofix,
    validate_scene_code,
)

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_CORPUS = os.path.join(_REPO_ROOT, "eval", "corpus")
DEFAULT_OUT = os.path.join(_REPO_ROOT, "eval", "results")


def _absent(pattern: str) -> Callable[[str], bool]:
    """Defect is resolved when `pattern` no longer appears in the repaired code."""
    rx = re.compile(pattern)
    return lambda code: not rx.search(code)


def _present(pattern: str) -> Callable[[str], bool]:
    """Defect is resolved when `pattern` DOES appear (an injection-style fix)."""
    rx = re.compile(pattern)
    return lambda code: bool(rx.search(code))


# Per-failure-mode probe for "is the specific defect actually gone?".
# Keyed by the sidecar `failure_mode`. Each probe is deliberately narrow: it
# asserts the exact broken construct named in the sidecar evidence, nothing else.
_DEFECT_PROBES: dict[str, Callable[[str], bool]] = {
    # --- undefined names: resolved by the injected role header ---------------
    "undefined_color_role": lambda c: all(
        not re.search(rf"\b{role}\b", c) or re.search(rf"^{role}\s*=", c, re.M)
        for role in ("PRIMARY", "SECONDARY", "STRUCT", "INK", "MUTED",
                     "SUCCESS", "WARNING", "ALERT")
    ),
    "undefined_easing_function": _absent(r"\bease_out_sine\b"),
    "nonexistent_color_constant": _absent(r"\b(DARK_GREY|DARK_GRAY|LIGHT_GREY|LIGHT_GRAY)\b"),
    # --- ManimCommunity symbols / methods ------------------------------------
    "manimcommunity_symbol_MathTex": _absent(r"\bMathTex\s*\("),
    "manimcommunity_symbol_Create": _absent(r"(?<!Show)\bCreate\s*\("),
    "manimcommunity_symbol_Circumscribe": _absent(r"\bCircumscribe\s*\("),
    "wrong_import_root": _absent(r"\bfrom\s+manim\s+import\s+\*"),
    "manimcommunity_attr_camera_frame": _absent(r"self\.camera\.frame"),
    "manimcommunity_method_set_fill_color": _absent(r"\.set_fill_color\s*\("),
    "manimcommunity_method_get_graph_point": _absent(r"\.get_graph_point\s*\("),
    "manimcommunity_method_get_point_at_angle": _absent(r"\.get_point_at_angle\s*\("),
    "manimcommunity_kwarg_x_length": _absent(r"\b(x_length|y_length)\s*="),
    "private_attr_mobjects": _absent(r"\._mobjects\b"),
    "nonexistent_method_begin_ambient_camera_rotation": _absent(
        r"begin_ambient_camera_rotation"
    ),
    "nonexistent_method_set_camera_orientation": _absent(r"set_camera_orientation"),
    "nonexistent_method_get_parts_by_tex_expression": _absent(
        r"get_parts_by_tex_expression"
    ),
    "nonexistent_method_add_fixed_in_frame_mobjects": _absent(
        r"add_fixed_in_frame_mobjects"
    ),
    "nonexistent_method_set_text": _absent(r"\.set_text\s*\("),
    # --- rejected kwargs ------------------------------------------------------
    "unexpected_kwarg_checkerboard_colors": _absent(r"\bcheckerboard_colors\s*="),
    "unexpected_kwarg_z_length": _absent(r"\bz_length\s*="),
    # x_axis_config is a VALID ManimGL Axes kwarg (established in 452cec7:
    # "Leave x_axis_config untouched"). The original crash had a different root
    # cause. Correct behavior is therefore to PRESERVE it — the blind
    # error-aware kwarg strip deletes valid code, which is a silent regression,
    # not a repair. Probe asserts survival.
    "unexpected_kwarg_x_axis_config": _present(r"\bx_axis_config\s*="),
    "banned_kwarg_tip_length": _absent(r"\btip_length\s*="),
    "banned_kwarg_corner_radius": _absent(r"\bcorner_radius\s*="),
    "banned_kwarg_scale_factor": _absent(r"\bscale_factor\s*="),
    "invalid_kwarg_font_on_tex": _absent(r"(?:Tex|TexText)\([^)]*\bfont\s*="),
    "invalid_kwarg_label_on_numberline": _absent(r"NumberLine\([^)]*\blabel\s*="),
    "wrong_kwarg_names_arrange_in_grid": _absent(
        r"arrange_in_grid\([^)]*\b(rows|cols|row_buff|col_buff)\s*="
    ),
    "wrong_kwarg_names_reorient": _absent(r"\b(theta_deg|phi_deg)\s*="),
    "unexpected_kwarg_with_did_you_mean": _absent(r"\btick_size\s*="),
    "invalid_axis_config_font_size": _absent(
        r"axis_config\s*=\s*\{[^{}]*[\"']font_size[\"']"
    ),
    # --- structural / syntax --------------------------------------------------
    "self_inflicted_syntax_error": _absent(r"resolution=20, BLUE_E\]"),
    "syntax_error_leading_comma_in_args": _absent(r"\(\s*,"),
    # #55 anti-regression: the VALID kwargs must SURVIVE, and stay parseable.
    "self_inflicted_duplicate_kwarg": _present(
        r"Square\([^)]*fill_color\s*=[^)]*fill_opacity\s*="
    ),
    "zero_length_arrow": _absent(r"Arrow\(\s*ORIGIN\s*,\s*ORIGIN\s*[,)]"),
    "multi_arg_fade": _absent(r"FadeOut\(\s*\w+\s*,\s*\w+\s*\)"),
    "become_passed_to_play": _absent(r"self\.play\([^)]*\w+\.become\("),
    "bare_rect_in_play": _absent(
        r"self\.play\(\s*(?:Surrounding|Background)Rectangle\s*\("
    ),
    "vgroup_item_assignment": _absent(r"\bboxes\[\w+\]\s*,\s*boxes\[\w+\]\s*="),
    "tex_value_readback": _absent(r"\.get_tex_string\s*\("),
    # --- value / type coercion ------------------------------------------------
    "color_gradient_float_length": _present(r"color_gradient\([^)]*int\("),
    "nonpositive_wait": _absent(r"self\.wait\(\s*(?:-\s*[\d.]+|0)\s*\)"),
    "off_scale_font_size": _absent(r"font_size\s*=\s*(?:53|31)\b"),
    "raw_palette_hex_literal": _absent(r'"#(?:00D9FF|FF6B35)"'),
    "y_axis_include_numbers_true": _absent(
        r'y_axis_config\s*=\s*\{[^}]*"include_numbers"\s*:\s*True'
    ),
    "latex_outer_text_wrapper": _absent(r"Tex\(\s*r?[\"']\\text\{"),
    "transform_matching_tex_on_text": _absent(r"TransformMatchingTex\(\s*counter\b"),
    "transform_point_count_mismatch": _absent(r"\bTransform\(old,"),
    "latex_unavailable_numeric_tex": _absent(r"Tex\(\s*(?:str\(|[\"']3\.14)"),
    # A correct split yields two separate single-animation play() calls. Probe
    # structurally: no single source LINE may contain both `.animate` and
    # `FadeIn(`, and no nested `self.play(self.play(` may be produced (the
    # current splitter emits exactly that, leaving an unclosed paren).
    "mixed_animate_and_fade_in_play": lambda c: (
        not any(".animate" in ln and "FadeIn(" in ln for ln in c.splitlines())
        and "self.play(self.play(" not in c
    ),
    # --- layout defects: detection-only, never rewritten ----------------------
    "layout_title_too_wide": _absent(
        r"The Algorithm: Pointers, Midpoint, and Comparison"
    ),
    "layout_side_by_side_array_collision": _absent(r"shift\(LEFT \* 2\.8\)"),
    "layout_top_edge_collision": _absent(r"Geometric View"),
    "layout_axes_default_size": _present(r"\.set_width\("),
    "layout_horizontal_chain_overflow": _absent(r"next_to\(eq2, RIGHT\)"),
    "layout_next_to_stacking": _absent(
        r"next_to\(line, DOWN\)[\s\S]*next_to\(line, DOWN\)"
    ),
    "timing_loop_unaccounted": _present(r"\+="),
}


def load_corpus(corpus_dir: str) -> list[dict[str, Any]]:
    """Load every case (.py + .json sidecar) from the corpus directory."""
    cases = []
    for name in sorted(os.listdir(corpus_dir)):
        if not name.endswith(".json"):
            continue
        sidecar = os.path.join(corpus_dir, name)
        scene = sidecar[:-5] + ".py"
        if not os.path.exists(scene):
            raise FileNotFoundError(f"sidecar {sidecar} has no matching .py scene")
        with open(sidecar) as f:
            meta = json.load(f)
        with open(scene) as f:
            meta["code"] = f.read()
        cases.append(meta)
    if not cases:
        raise ValueError(f"no corpus cases found in {corpus_dir}")
    return cases


def repair(code: str, stderr: str) -> tuple[str, list[str]]:
    """Run Codeguard's real static repair path over one case.

    Proactive pass always runs (mirrors scene_generator / precheck_and_autofix_file).
    The reactive pass runs only when the case carries a render traceback, mirroring
    retry.py, which calls apply_error_aware_fixes with the captured stderr.
    """
    _, applied = apply_known_fixes(code)  # capture rule labels for attribution
    repaired = precheck_and_autofix(code)
    if stderr:
        repaired, ea_applied = apply_error_aware_fixes(repaired, stderr)
        applied = applied + [f"[error-aware] {a}" for a in ea_applied]
    return repaired, applied


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Repair one case and score whether the specific defect was resolved."""
    code = case["code"]
    mode = case["failure_mode"]
    repaired, applied = repair(code, case.get("stderr", ""))

    validation_errors = validate_scene_code(repaired)
    probe = _DEFECT_PROBES.get(mode)
    if probe is None:
        raise KeyError(
            f"no defect_probe registered for failure_mode '{mode}' "
            f"(case {case['case_id']}) — add one to _DEFECT_PROBES"
        )
    defect_cleared = probe(repaired)
    resolved = defect_cleared and not validation_errors

    # Guard against a probe that is simply too lenient. If the probe already
    # passes on the UNREPAIRED code, the case proves nothing about Codeguard —
    # the "win" is free. `regression_guard` cases are the deliberate exception:
    # there the correct behavior is to leave valid code untouched (#55), so the
    # probe passing before and after IS the assertion.
    probe_passed_before = probe(code)
    is_regression_guard = case.get("expected_outcome") == "regression_guard"
    if probe_passed_before and not is_regression_guard:
        raise AssertionError(
            f"case {case['case_id']}: defect probe for '{mode}' already passes on the "
            "unrepaired source, so it cannot measure whether Codeguard fixed anything. "
            "Tighten the probe, or label the case expected_outcome='regression_guard'."
        )

    reason = None
    if not resolved:
        if not defect_cleared and validation_errors:
            reason = "defect remains and validation still fails"
        elif not defect_cleared:
            reason = "no rule rewrote the defect (detection-only or unhandled)"
        else:
            reason = f"repair left validation errors: {validation_errors[0]}"

    return {
        "case_id": case["case_id"],
        "failure_mode": mode,
        "error_class": case["error_class"],
        "source": case["provenance"]["source"],
        "expected_outcome": case["expected_outcome"],
        "resolved": resolved,
        "code_changed": repaired != code,
        "defect_cleared": defect_cleared,
        "validation_clean": not validation_errors,
        "validation_errors": validation_errors,
        "rules_fired": applied,
        "unresolved_reason": reason,
    }


def _rate(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(results)
    resolved = sum(r["resolved"] for r in results)

    by_source: dict[str, dict[str, Any]] = {}
    for src in sorted({r["source"] for r in results}):
        rows = [r for r in results if r["source"] == src]
        ok = sum(r["resolved"] for r in rows)
        by_source[src] = {"total": len(rows), "resolved": ok,
                          "rate_pct": _rate(ok, len(rows))}

    by_mode: dict[str, dict[str, Any]] = {}
    for mode in sorted({r["failure_mode"] for r in results}):
        rows = [r for r in results if r["failure_mode"] == mode]
        ok = sum(r["resolved"] for r in rows)
        by_mode[mode] = {"total": len(rows), "resolved": ok,
                         "rate_pct": _rate(ok, len(rows))}

    by_error_class: dict[str, dict[str, Any]] = {}
    for ec in sorted({r["error_class"] for r in results}):
        rows = [r for r in results if r["error_class"] == ec]
        ok = sum(r["resolved"] for r in rows)
        by_error_class[ec] = {"total": len(rows), "resolved": ok,
                              "rate_pct": _rate(ok, len(rows))}

    rule_counter: Counter[str] = Counter()
    for r in results:
        for rule in r["rules_fired"]:
            # strip the trailing "(N)" occurrence count for aggregation
            rule_counter[re.sub(r"\s*\(\d+\)$", "", rule)] += 1

    unresolved = [
        {"case_id": r["case_id"], "failure_mode": r["failure_mode"],
         "source": r["source"], "expected_outcome": r["expected_outcome"],
         "reason": r["unresolved_reason"]}
        for r in results if not r["resolved"]
    ]

    # Cases where the sidecar's expectation disagreed with the measurement.
    surprises = [
        {"case_id": r["case_id"], "expected": r["expected_outcome"],
         "measured": "resolved" if r["resolved"] else "unresolved"}
        for r in results
        if r["expected_outcome"] != "regression_guard"
        and (r["expected_outcome"] == "repairable") != r["resolved"]
    ]

    return {
        "overall": {"total": total, "resolved": resolved,
                    "rate_pct": _rate(resolved, total)},
        "by_source": by_source,
        "by_failure_mode": by_mode,
        "by_error_class": by_error_class,
        "rules_fired": dict(rule_counter.most_common()),
        "unresolved": unresolved,
        "expectation_mismatches": surprises,
    }


def _md(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    o = summary["overall"]
    L: list[str] = []
    L.append("# Codeguard resolution rate — measured\n")
    L.append(
        "Generated by `eval/run_corpus.py` over `eval/corpus/`. "
        "Regenerate with `python3 eval/run_corpus.py`.\n"
    )
    L.append(
        f"## Overall: **{o['rate_pct']}%** "
        f"({o['resolved']}/{o['total']} cases resolved)\n"
    )
    L.append(
        "A case counts as resolved only when BOTH hold: the case's specific defect "
        "marker is gone from the repaired source, AND `validate_scene_code()` on the "
        "repaired code returns no errors. Passing validation alone is not sufficient — "
        "22 of these cases pass `validate_scene_code` before any repair runs, because "
        "static validation cannot see ManimGL runtime defects like `x_length=` or "
        "`DARK_GREY`. Scoring on validation alone would inflate the number with cases "
        "Codeguard never touched.\n"
    )
    L.append("## By corpus source\n")
    L.append("This split matters: cases written by hand against the fix rules "
             "partly determine their own pass rate.\n")
    L.append("| source | cases | resolved | rate |")
    L.append("|---|---|---|---|")
    for src, s in summary["by_source"].items():
        L.append(f"| `{src}` | {s['total']} | {s['resolved']} | {s['rate_pct']}% |")
    L.append("")
    L.append("## By error class\n")
    L.append("| error class | cases | resolved | rate |")
    L.append("|---|---|---|---|")
    for ec, s in summary["by_error_class"].items():
        L.append(f"| `{ec}` | {s['total']} | {s['resolved']} | {s['rate_pct']}% |")
    L.append("")
    L.append("## By failure mode\n")
    L.append("| failure mode | cases | resolved | rate |")
    L.append("|---|---|---|---|")
    for mode, s in summary["by_failure_mode"].items():
        L.append(f"| `{mode}` | {s['total']} | {s['resolved']} | {s['rate_pct']}% |")
    L.append("")
    L.append("## Fix rules that fired most\n")
    L.append("| rule | cases |")
    L.append("|---|---|")
    for rule, n in list(summary["rules_fired"].items())[:25]:
        L.append(f"| `{rule}` | {n} |")
    L.append("")
    L.append(f"## Unresolved cases ({len(summary['unresolved'])})\n")
    L.append("| case | failure mode | source | expected | why not fixed |")
    L.append("|---|---|---|---|---|")
    for u in summary["unresolved"]:
        L.append(
            f"| `{u['case_id']}` | `{u['failure_mode']}` | {u['source']} "
            f"| {u['expected_outcome']} | {u['reason']} |"
        )
    L.append("")
    if summary["expectation_mismatches"]:
        L.append("## Cases where measurement disagreed with the label\n")
        L.append("| case | sidecar expected | measured |")
        L.append("|---|---|---|")
        for m in summary["expectation_mismatches"]:
            L.append(f"| `{m['case_id']}` | {m['expected']} | {m['measured']} |")
        L.append("")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default=DEFAULT_CORPUS)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    cases = load_corpus(args.corpus)
    results = [run_case(c) for c in cases]
    summary = summarize(results)

    os.makedirs(args.out, exist_ok=True)
    payload = {"summary": summary, "cases": results}
    with open(os.path.join(args.out, "codeguard.json"), "w") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")
    with open(os.path.join(args.out, "codeguard.md"), "w") as f:
        f.write(_md(summary, results))

    if not args.quiet:
        o = summary["overall"]
        print(f"Codeguard resolution rate: {o['rate_pct']}% "
              f"({o['resolved']}/{o['total']})")
        for src, s in summary["by_source"].items():
            print(f"  {src:26s} {s['rate_pct']:5.1f}%  ({s['resolved']}/{s['total']})")
        print(f"  unresolved: {len(summary['unresolved'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
