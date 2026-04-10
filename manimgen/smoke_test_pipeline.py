"""
Full pipeline smoke test — zero LLM calls, real everything else.

Tests every unit in sequence:
  cue_parser → TTS → segmenter → audio_slicer → codeguard →
  manimgl render → cutter → muxer → assembler → final video

The scene code is hand-written (like an example), so we're not testing
LLM quality — we're testing that all the plumbing works together correctly.

Run:
    python3 smoke_test_pipeline.py
"""

import os, sys, shutil, json, subprocess
sys.path.insert(0, ".")

OUT = "/tmp/manimgen_pipeline_smoke"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

SCENE_TEMPLATE = """\
from manimlib import *

class PipelineSmokeScene(Scene):
    def construct(self):
        title = Text("Binary Search", font_size=48).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)

        # CUE 0 — array appears (animation=1.5s, wait fills remaining cue duration)
        values = [1, 3, 5, 7, 9, 11, 13, 15]
        boxes = VGroup(*[
            VGroup(
                Square(side_length=0.72, color=GREY_B, fill_color="#1C1C1C", fill_opacity=1),
                Text(str(v), font_size=24, color=WHITE),
            )
            for v in values
        ])
        for box in boxes:
            box[1].move_to(box[0])
        boxes.arrange(RIGHT, buff=0.1).center().shift(DOWN * 0.3)
        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.12), run_time=1.5)
        self.wait({wait0:.2f})

        # CUE 1 — highlight mid, dim halves (animation=1.8s, wait fills remaining)
        mid = 3
        mid_rect = SurroundingRectangle(boxes[mid], color=YELLOW, buff=0.05)
        mid_label = Text("mid", font_size=22, color=YELLOW).next_to(boxes[mid], UP, buff=0.2)
        self.play(ShowCreation(mid_rect), Write(mid_label), run_time=0.6)
        self.play(*[b.animate.set_opacity(0.2) for b in boxes[:mid]], run_time=0.5)
        found_label = Text("Target is in right half", font_size=28, color=GREEN)
        found_label.next_to(boxes, DOWN, buff=0.5)
        self.play(Write(found_label), run_time=0.7)
        self.wait({wait1:.2f})

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
"""

def check(label, condition, detail=""):
    if not condition:
        print(f"  ✗ FAIL: {label}" + (f" — {detail}" if detail else ""))
        sys.exit(1)
    print(f"  ✓ {label}")

print("=" * 60)
print("UNIT 1: cue_parser")
print("=" * 60)
from manimgen.planner.cue_parser import parse_cues

narration = (
    "Binary search is a fast algorithm for sorted arrays. "
    "It works by checking the middle element every step. "
    "[CUE] "
    "If the target is larger, we discard the left half entirely. "
    "This halves the search space each time, giving us log n performance."
)
clean_text, cues = parse_cues(narration)
check("parse_cues returns clean text with no [CUE] tags", "[CUE]" not in clean_text)
check("2 cue word indices", len(cues) == 2)
check("cue 0 starts at word 0", cues[0] == 0)
check("cue 1 index > 0", cues[1] > 0)
print(f"  cue word indices: {cues}")

print()
print("=" * 60)
print("UNIT 2: TTS (edge-tts, free)")
print("=" * 60)
from manimgen.renderer.tts import generate_narration, get_audio_duration

audio_path = os.path.join(OUT, "narration.mp3")
_, timestamps = generate_narration(clean_text, audio_path)
duration = get_audio_duration(audio_path)
check("audio file written", os.path.exists(audio_path))
check("timestamps non-empty", len(timestamps) > 0)
check("duration > 0", duration > 0)
print(f"  {len(timestamps)} word timestamps, {duration:.1f}s audio")

print()
print("=" * 60)
print("UNIT 3: segmenter")
print("=" * 60)
from manimgen.planner.segmenter import compute_segments

segments = compute_segments(timestamps, cues, duration)
check("2 segments", len(segments) == 2)
check("all durations positive", all(s.duration > 0 for s in segments))
total = sum(s.duration for s in segments)
check(f"segments cover full audio (total={total:.2f}s, audio={duration:.2f}s)",
      abs(total - duration) < 1.5)
for s in segments:
    print(f"  cue {s.cue_index}: start={s.start_time:.2f}s  duration={s.duration:.2f}s")
cue_start_times = [s.start_time for s in segments]
cue_durations   = [s.duration   for s in segments]

print()
print("=" * 60)
print("UNIT 4: audio_slicer")
print("=" * 60)
from manimgen.renderer.audio_slicer import slice_audio

slices = slice_audio(audio_path, segments, OUT, "smoke")
check("2 audio slices", len(slices) == 2)
for p in slices:
    check(f"slice exists: {os.path.basename(p)}", os.path.exists(p))

# Inject actual cue durations into scene so self.wait() matches TTS exactly
ANIM_TIME_CUE0 = 1.5   # run_time of LaggedStart FadeIn
ANIM_TIME_CUE1 = 1.8   # run_time of ShowCreation + opacity + Write
FADEOUT_TIME   = 0.8
wait0 = max(0.0, cue_durations[0] - ANIM_TIME_CUE0)
wait1 = max(0.0, cue_durations[1] - ANIM_TIME_CUE1 - FADEOUT_TIME)
SCENE_CODE = SCENE_TEMPLATE.format(wait0=wait0, wait1=wait1)
print(f"\n  cue 0 duration={cue_durations[0]:.2f}s → self.wait({wait0:.2f})")
print(f"  cue 1 duration={cue_durations[1]:.2f}s → self.wait({wait1:.2f})")

print()
print("=" * 60)
print("UNIT 5: codeguard on hand-written scene")
print("=" * 60)
from manimgen.validator.codeguard import apply_known_fixes, validate_scene_code, precheck_and_autofix

fixed_code, fixes = apply_known_fixes(SCENE_CODE)
errors = validate_scene_code(fixed_code)
check("no banned patterns in scene", len(errors) == 0, str(errors))
check("no ._mobjects", "._mobjects" not in fixed_code)
check("no get_tex_string", ".get_tex_string" not in fixed_code)
print(f"  fixes applied: {fixes or 'none (scene was already clean)'}")

print()
print("=" * 60)
print("UNIT 6: ManimGL render")
print("=" * 60)
scene_path = os.path.join(OUT, "pipeline_smoke_scene.py")
with open(scene_path, "w") as f:
    f.write(fixed_code)

render = subprocess.run(
    ["manimgl", scene_path, "PipelineSmokeScene", "-w", "--hd", "-c", "#1C1C1C"],
    capture_output=True, text=True
)
check("render exit code 0", render.returncode == 0, render.stderr[-500:] if render.returncode != 0 else "")

# Find the rendered mp4
rendered_mp4 = None
for root, dirs, files in os.walk("videos"):
    for f in files:
        if "PipelineSmokeScene" in f and f.endswith(".mp4"):
            rendered_mp4 = os.path.join(root, f)
check("rendered .mp4 exists", rendered_mp4 is not None)
print(f"  rendered: {rendered_mp4}")

print()
print("=" * 60)
print("UNIT 7: cutter (slice rendered video at cue boundaries)")
print("=" * 60)
from manimgen.renderer.cutter import cut_video_at_cues

clips = cut_video_at_cues(rendered_mp4, cue_start_times, cue_durations, OUT, "smoke")
check(f"2 video clips", len(clips) == 2)
for p in clips:
    check(f"clip exists: {os.path.basename(p)}", os.path.exists(p))

print()
print("=" * 60)
print("UNIT 8: muxer (audio + video per cue)")
print("=" * 60)
from manimgen.renderer.muxer import mux_audio_video

muxed = []
for i, (vclip, aclip) in enumerate(zip(clips, slices)):
    out_path = os.path.join(OUT, f"muxed_cue{i:02d}.mp4")
    result = mux_audio_video(vclip, aclip, out_path)
    muxed.append(result)
    check(f"muxed cue {i}: {os.path.basename(result)}", os.path.exists(result))

print()
print("=" * 60)
print("UNIT 9: assembler (final video)")
print("=" * 60)
from manimgen.renderer.assembler import assemble_video

final_path = assemble_video(muxed, "pipeline_smoke_binary_search")
check("final video exists", os.path.exists(final_path))
size_kb = os.path.getsize(final_path) // 1024
check(f"final video non-empty ({size_kb}KB)", size_kb > 10)

print()
print("=" * 60)
print(f"ALL UNITS PASSED — opening final video")
print("=" * 60)
print(f"\n  {final_path}\n")
subprocess.run(["open", final_path])
