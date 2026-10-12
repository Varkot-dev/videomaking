# Overlap probe calibration (#97)

Record of how `OVERLAP_THRESHOLD` in `manimgen/probes/overlap_probe.py` was
chosen. Renders: manimgl 1.7.2, draft quality (`-l`), Xvfb, through
`render_command.run_manimgl` with the probe on. No LLM calls.

`first_run_section_02.py` and `first_run_section_05.py` are unmodified copies
of two scenes from the first real end-to-end run (binary search topic).

## First real run, six sections

"fraction" is the intersection area over the smaller text box.

| Scene | Findings (text vs text) | Judged from frames |
|---|---|---|
| section_01 | none | clean (sampled frames) |
| section_02 | caption "One comparison. Eight cards gone." over the dimmed row of number cards, fraction 1.0 (10 pairs, capped to 3 per string) | real defect, seen in a frame extracted at t=17s; not in the 12 sampled frames |
| section_03 | none | clean (three sampled frames) |
| section_04 | none | clean (two sampled frames) |
| section_05 | code lines vs "90%" (0.40), vs "of professional programmers failed..." (0.55, 0.35), "return -1" vs "(Bentley, Programming Pearls)" (0.57) | real defect (b), the known one |
| section_06 | none | clean (one sampled frame) |

Largest fraction seen below the threshold: 0.138 (section_05, code line
"lo = 0; hi = n - 1" vs "90%", part of the same real defect).

Known defect (a), the garbled "mid = -794,967,296": frames extracted around it
show it is the midpoint of a 0.6 s `TransformMatchingTex` between unrelated
equations. Every settled state before and after is clean, so the settled-state
text check does not report it. The probe also scores each
`TransformMatchingTex` at its midpoint (`morphs` in the report): this one
scores 0.399, but the other morphs in the run score 0.03 to 0.22 and ALL of
them looked garbled at their midpoints in extracted frames, so no threshold
separates "the bad one" from ordinary morphs. The score is kept as a
diagnostic only (`MORPH_THRESHOLD = None`).

## Bundled examples (`manimgen/examples/`, 31 files)

27 rendered; 4 fail to render on this manimgl with or without the probe
(brace_annotation, color_fill, epsilon_delta, number_line: API errors).

| Example | Finding | Judgement |
|---|---|---|
| array_swap_scene | "Swaps: 1" / "Swaps: 2" / "Swaps: 3" stacked, fraction 1.0 | real: `FadeTransform` then `become` leaves the old counter on screen; visible as a garbled digit |
| value_tracker_scene | coordinate label vs axis number "0.00", 0.30 | real: label drawn over the axis numbers in the frame |
| graph_scene | title vs y-axis number "4.00", 0.218 | real: visible in the last frame |
| the other 24 | none | |

No clean text pair with a non-zero overlap was observed in any of the 33
rendered scenes, so the data gives no lower bound for the threshold. 0.20 was
chosen: it reports every real overlap above (lowest 0.218) and leaves margin
over grazing boxes, which `MIN_OVERLAP_AREA` (0.02 square units) filters
anyway. The false-positive rate on other topics is unmeasured.

## Overhead

Same three scenes (section_04, 05, 06) rendered alternately with the probe off
and on, two rounds: off 145.3 s and 157.9 s, on 159.2 s and 159.2 s in total.
The difference is within the run-to-run noise of the off runs (about 9%).
