# START HERE — Project Roadmap

**You are here. This file is the map. Read this first.**

On 2026-05-19, a fleet of review + research agents went through the entire
codebase. This folder is the result. Below is everything, in plain English,
and where to find it.

---

## 1. What's in this folder

| File | What it is | Read it when |
|---|---|---|
| **START_HERE.md** | This map. | Now. Always start here. |
| **what-to-work-on-next.md** | The next 3–4 days of work, in priority order. Each task = what it fixes, which files, how risky, how to test. | You want to know "what do I do now?" |
| **2-week-plan.md** | The bigger picture: the one root problem in the codebase and the 2-week plan to fix it. | You want to understand the strategy. |
| **how-we-found-this.md** | The plan for the analysis itself (how the agent fleet was run). | You want to know how this was produced. Reference only. |

## 2. Where the actual to-do list lives

The **21 concrete tasks are GitHub Issues**, not files. Open them here:

👉 **https://github.com/Varkot-dev/videomaking/issues**

Filter with these labels (on GitHub, click "Labels"):
- `severity:critical` → the 8 things that are actively broken or dangerous
- `north-star:quality` → things that make the **video look better**
- `north-star:extensibility` → things that make the code **safe to add features to**
- `north-star:ship` → security / demo-readiness

## 3. The one thing to understand

Four reviewers independently found the **same root problem**:

> The code already has a "checker" layer that *computes the correct answers*
> (cue timing, etc.) — but instead of **enforcing** them, it hands them back
> to the AI as a *suggestion*. The AI then often ignores them. That single
> design mistake is why videos get the 1–8 second frozen-screen bug.

Fixing that is the spine of the 2-week plan.

## 4. If you only do ONE thing first

**GitHub issue #23** ("F5: timing evaluator..."). It is not a future
improvement — it is **actively breaking correct videos right now**: a
correctly-made scene gets mis-detected as broken, the AI is asked to "fix"
it, and it makes it worse. It's a ~2-hour fix. Everything else in the
timing work depends on it being done first.

## 5. The recommended order (from what-to-work-on-next.md)

```
Day 1 (all can be done in parallel — they don't touch the same files):
  • Issue #23 — F5 timing fix          ← DO THIS FIRST (active bug)
  • Issue #26 — stop leaking API keys to the render process
  • Issue #25 — stop the AI from writing files to dangerous paths
  • Issue #35 — make the test suite honest

Day 2:
  • Issue #24 — make the freeze-frame check actually run on every video
  • Issues #28/#29/#33 — stop the pipeline silently shipping broken video

Day 3:
  • Issue #22 — make the timing checker authoritative (the big quality fix)
  • Issue #30 — replace the brittle "banned-list" with an "allowed-list"

Day 3–4:
  • Issue #31 — untangle the 164-line function (the extensibility spine)
```

Full detail (files, risk, test plan, why this order) is in
**what-to-work-on-next.md** in this folder.

## 6. How to come back to this later

It's at `docs/roadmap/` in the project. From a terminal in the project
folder: `open docs/roadmap/START_HERE.md` (macOS) — or just open the
`docs/roadmap` folder in your editor. On GitHub:
`https://github.com/Varkot-dev/videomaking/tree/main/docs/roadmap`
