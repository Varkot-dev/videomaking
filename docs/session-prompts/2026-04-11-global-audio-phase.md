# Session Prompt: Global Audio Phase + Director Context

## What you're building

Refactor the manimgen pipeline so all TTS audio is generated **upfront for the entire video** before any section starts rendering, and the Director gets **global video context** (position, total duration, pacing) when it writes each section's scene code.

Right now the pipeline does:
```
Section 1: TTS → segment → codegen → render → cut → mux
Section 2: TTS → segment → codegen → render → cut → mux
...
```

After this change it should do:
```
PHASE 1 (global):
  Plan → TTS all sections → compute all cue durations → build overview

PHASE 2 (per-section):
  For each section: codegen (with overview context) → render → cut → mux
```

---

## Repo and environment

- **Repo:** `https://github.com/Varkot-dev/videomaking.git` — branch `main`
- **Working directory:** `/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen`
- **Source package:** `manimgen/manimgen/manimgen/` — this is the importable package. Never edit files outside this path (no top-level mirrors).
- **Tests:** `manimgen/manimgen/tests/` — run with:
  ```bash
  python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q
  ```
  Currently: **463 tests passing**. Do not break this.
- **Stack:** Python 3.13, ManimGL (3b1b fork), Gemini 2.5 Flash, edge-tts, FFmpeg

---

## Skills to use

You have superpowers. Use these skills:

- **`writing-plans`** — before writing any code, create a full implementation plan
- **`test-driven-development`** — write tests first for new functions, then implement
- **`requesting-code-review`** — run at the end once all changes are working
- **`verification-before-completion`** — run before declaring done

---

## Exact changes required

### File 1: `manimgen/manimgen/manimgen/cli.py`

This is the only orchestration file. Split `main()` into two explicit phases:

**Phase 1** — runs once, before any section loop:
```python
# Generate TTS for ALL sections upfront
all_section_audio = {}   # section_id → {audio_path, timestamps, audio_duration, segments, audio_slices}
for idx, section in enumerate(sections, start=1):
    result = _run_tts_for_section(section, idx)
    if result:
        audio_path, timestamps, audio_duration = result
        segments = compute_segments(timestamps, cue_word_indices, audio_duration)
        audio_slices = slice_audio(...)
        all_section_audio[section_id] = {
            "audio_path": audio_path,
            "timestamps": timestamps,
            "audio_duration": audio_duration,
            "segments": segments,
            "audio_slices": audio_slices,
            "cue_durations": [seg.duration for seg in segments],
        }

# Build global overview
overview = _build_overview(lesson_plan, all_section_audio)
```

**`_build_overview()`** returns a dict like:
```python
{
    "total_duration": 487.3,          # sum of all section audio durations
    "n_sections": 5,
    "sections": [
        {
            "id": "section_01",
            "title": "...",
            "duration": 94.2,          # audio duration in seconds
            "n_cues": 4,
            "position": "1 of 5",
        },
        ...
    ],
    "pacing_notes": "..."              # e.g. "Section 2 is longest at 2m12s"
}
```

**Phase 2** — per-section loop passes pre-computed audio data and overview:
```python
for idx, section in enumerate(sections, start=1):
    section_audio = all_section_audio.get(section_id)   # may be None if TTS off/failed
    _run_section(section, idx, tts_on, current_topic_hash, section_audio, overview)
```

**`_run_section()`** signature changes to:
```python
def _run_section(section, idx, tts_on, current_topic_hash, section_audio=None, overview=None):
```
- Remove the TTS call from inside `_run_section` — it now receives pre-computed audio
- If `section_audio` is None (TTS off or failed), behaviour is unchanged (no audio)
- Pass `overview` through to `generate_scenes()`

### File 2: `manimgen/manimgen/manimgen/generator/scene_generator.py`

`generate_scenes()` already accepts `cue_durations`. Add `overview` as an optional parameter:

```python
def generate_scenes(section, cue_durations=None, overview=None):
```

Inject overview into the Director prompt as a brief context block. Add it to the user message, not the system prompt (system prompt is shared across all sections):

```
## Video context
This is section {n} of {total} in a {total_duration:.0f}s video.
Section duration: {section_duration:.0f}s across {n_cues} cues.
{pacing_notes if any}
```

Keep the injected text short — 3–5 lines max. The Director prompt is already long; don't bloat it.

---

## Constraints — read carefully

1. **One file at a time.** Make all changes to `cli.py` completely, run the tests, commit. Then move to `scene_generator.py`, run tests, commit. Never touch two files simultaneously.

2. **Minimal blast radius.** Only change what's necessary. Don't refactor, rename, or "clean up" anything not directly related to this feature.

3. **Backward compatible.** If TTS is disabled (`tts.enabled: false` in config.yaml), the pipeline must still work exactly as before. `section_audio=None` must be a fully-handled path everywhere.

4. **No new dependencies.** Don't add any new pip packages.

5. **Commit after each working file.** After `cli.py` tests pass: `git commit`. After `scene_generator.py` tests pass: `git commit`. Don't batch.

6. **Don't touch these files:** `codeguard.py`, `retry.py`, `assembler.py`, `timing_verifier.py`, `render_validator.py`, any test file you didn't create. The pipeline validation layer is done — leave it alone.

---

## Tests to write (TDD — write before implementing)

### For `cli.py` changes:
- `test_all_tts_runs_before_any_codegen` — mock `_run_tts_for_section` and `generate_scenes`, assert TTS was called for all sections before `generate_scenes` was called for section 1
- `test_build_overview_total_duration` — given mock section audio data, assert `_build_overview` sums durations correctly
- `test_build_overview_section_positions` — assert position strings are "1 of N", "2 of N", etc.
- `test_run_section_uses_precomputed_audio` — assert `_run_section` does NOT call `_run_tts_for_section` when `section_audio` is provided
- `test_tts_off_still_works` — with `tts_on=False`, pipeline completes without errors

### For `scene_generator.py` changes:
- `test_overview_injected_into_prompt` — mock `chat`, assert the user message contains "section N of M" when overview is passed
- `test_no_overview_still_works` — `generate_scenes(section)` with no overview arg works as before

---

## What NOT to do

- Do not move TTS to a separate process or thread
- Do not add a progress bar or spinner
- Do not change the audio file format, naming convention, or output paths
- Do not change how `segmenter`, `audio_slicer`, `cutter`, or `muxer` work
- Do not add a `--parallel` flag or any new CLI arguments
- Do not summarise what you did at the end of each response — just show the diff

---

## Definition of done

1. All 463 existing tests still pass (plus new ones you wrote)
2. `python3 -m pytest tests/ --ignore=tests/test_scene_generator.py --ignore=tests/test_planner.py --ignore=tests/test_pipeline_e2e.py -q` exits 0
3. `cli.py` Phase 1 runs all TTS before any codegen starts
4. `generate_scenes()` receives overview context and injects it into the Director user message
5. Two clean commits on `main`: one for `cli.py`, one for `scene_generator.py`
6. `requesting-code-review` skill has been run and any issues addressed
