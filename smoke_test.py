"""
Smoke test — visual output you can actually see.

What this proves:
1. codeguard catches and auto-fixes real bugs before rendering
2. ManimGL renders the result correctly
3. You can watch the output video

Run:
    python3 smoke_test.py
"""

import subprocess, sys, os, tempfile, shutil

sys.path.insert(0, ".")
from manimgen.validator.codeguard import apply_known_fixes, validate_scene_code

# ── A scene with deliberate bugs codeguard should catch ──────────────────────
BUGGY_SCENE = '''\
from manimlib import *

class SmokeTestScene(Scene):
    def construct(self):
        title = Text("Bubble Sort", font_size=44).to_edge(UP, buff=0.8)
        self.play(Write(title), run_time=1.0)

        values = [5, 3, 8, 1, 9]
        boxes = VGroup(*[
            VGroup(
                Square(side_length=0.75, color=GREY_B, fill_color="#1C1C1C", fill_opacity=1),
                Text(str(v), font_size=28),
            )
            for v in values
        ])
        for box in boxes:
            box[1].move_to(box[0])
        boxes.arrange(RIGHT, buff=0.15).center().shift(DOWN * 0.2)

        self.play(LaggedStart(*[FadeIn(b) for b in boxes], lag_ratio=0.15), run_time=1.5)

        # Bug 1: _mobjects (private attr, doesn't exist) — codeguard rewrites to .submobjects
        first_box = boxes._mobjects[0]

        # Bug 2: x_length= (ManimCommunity) instead of width= (ManimGL)
        axes = Axes(
            x_range=[-1, 6, 1],
            y_range=[0, 10, 2],
            x_length=8,
            y_length=4,
            axis_config={"color": GREY_B},
        )

        # Highlight pass — correct usage (no bugs here)
        highlight = SurroundingRectangle(boxes[0], color=YELLOW, buff=0.04)
        self.play(ShowCreation(highlight), run_time=0.3)
        self.play(highlight.animate.move_to(boxes[1]), run_time=0.25)
        self.play(highlight.animate.move_to(boxes[2]), run_time=0.25)
        self.wait(0.5)

        self.play(*[FadeOut(m) for m in self.mobjects], run_time=0.8)
'''

# ── Step 1: Show what codeguard finds and fixes ───────────────────────────────
print("=" * 60)
print("STEP 1: Codeguard analysis")
print("=" * 60)

fixed, applied = apply_known_fixes(BUGGY_SCENE)
errors_before = validate_scene_code(BUGGY_SCENE)
errors_after  = validate_scene_code(fixed)

print(f"\nBugs in original:")
for e in errors_before:
    print(f"  ✗ {e}")

print(f"\nAuto-fixes applied ({len(applied)}):")
for f in applied:
    print(f"  ✓ {f}")

print(f"\nErrors remaining after fix: {len(errors_after)}")
for e in errors_after:
    print(f"  ✗ {e}")

assert "._mobjects" not in fixed, "codeguard should have replaced ._mobjects"
assert "x_length=" not in fixed, "codeguard should have replaced x_length="
assert "width=" in fixed, "width= should now be present"
print("\n✓ All assertions passed — codeguard is working correctly")

# ── Step 2: Write fixed code to temp file and render ─────────────────────────
print("\n" + "=" * 60)
print("STEP 2: Rendering with ManimGL")
print("=" * 60)

out_dir = "/tmp/manimgen_smoke"
os.makedirs(out_dir, exist_ok=True)
scene_path = os.path.join(out_dir, "smoke_scene.py")

with open(scene_path, "w") as f:
    f.write(fixed)

print(f"\nWriting fixed scene to: {scene_path}")
print("Running manimgl render...\n")

result = subprocess.run(
    ["manimgl", scene_path, "SmokeTestScene", "-w", "--hd", "-c", "#1C1C1C"],
    capture_output=True, text=True
)

if result.returncode != 0:
    print("RENDER FAILED:")
    print(result.stderr[-2000:])
    sys.exit(1)

# Find the output video
video_path = None
for root, dirs, files in os.walk(os.path.expanduser("~")):
    # manimgl outputs to ~/Videos or ./videos
    break

# Check common output locations
for search_dir in ["videos", "/tmp/manimgen_smoke", os.path.expanduser("~/Videos")]:
    for root, dirs, files in os.walk(search_dir):
        for fname in files:
            if "SmokeTestScene" in fname and fname.endswith(".mp4"):
                video_path = os.path.join(root, fname)
                break

if not video_path:
    # fallback: search cwd
    for root, dirs, files in os.walk("."):
        for fname in files:
            if "SmokeTestScene" in fname and fname.endswith(".mp4"):
                video_path = os.path.join(root, fname)

if video_path:
    print(f"✓ Rendered: {video_path}")
    print("\nOpening video...")
    subprocess.run(["open", video_path])
else:
    print("Render succeeded but could not locate output .mp4")
    print("stdout:", result.stdout[-500:])

print("\n✓ Smoke test complete")
