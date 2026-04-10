import os
import sys

# Ensure correct PYTHONPATH
repo_dir = "/Users/varshithkotagiri/Projects/3Blue1Brown"
if repo_dir not in sys.path:
    sys.path.insert(0, repo_dir)

from manimgen.validator.retry import retry_scene
import manimgen.llm

# Phase 2: Intercept payload explicitly
original_chat = manimgen.llm.chat
def intercepted_chat(system, user, images=None):
    if images:
        print(f"\n[PHASE 2 - PAYLOAD VERIFIED] Intercepted chat() call!")
        print(f"Total Base64 Images sent: {len(images)}")
        if "The FIRST" in user:
            print(f"Context injected: Vision Evaluation Model\nPrompt Intro: '{user[:150]}...'")
        elif "This ManimGL scene rendered successfully but has visual defects" in user:
            print(f"Context injected: Retry Fixer Model\nPrompt Intro: '{user[:150]}...'")
    return original_chat(system, user, images=images)

manimgen.llm.chat = intercepted_chat

def main():
    scene_path = "/Users/varshithkotagiri/Projects/3Blue1Brown/manimgen/manimgen/output/scenes/section_05.py"
    if not os.path.exists(scene_path):
        print(f"File not found: {scene_path}")
        return

    with open(scene_path, "r") as f:
        code = f.read()

    system_prompt = "You are a ManimGL animator. Generate and fix scenes ensuring no text cutoff."

    print(f"\n[PHASE 1 - TORTURE TEST] Launching exact retry loop against {scene_path}...")
    # Trigger `retry_scene` which invokes manimgl, extract frames, evaluate layout, and if defective, call LLM
    mock_section = {"id": "section_05", "title": "A Step-by-Step Example"}
    ok, video = retry_scene(mock_section, code, "Section05Scene", scene_path, cue_durations=[6.0, 7.0, 10.0])
    
    print("\n[PHASE 3 - INTEGRATION RESULT]")
    print("Visual check passed natively?" if ok else "Visual check required further retries.")
    print(f"Final Video generated at: {video}")

if __name__ == "__main__":
    main()
