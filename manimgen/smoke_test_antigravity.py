import os
import sys
import subprocess

# Ensure we can import the project
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Ensure the correct ffmpeg is injected into the sys path (it exists at /opt/homebrew/bin/ffmpeg)
os.environ["PATH"] = "/opt/homebrew/bin:" + os.environ.get("PATH", "")

# Mock the environment variables so dotenv missing won't fail it
os.environ["LLM_PROVIDER"] = "gemini"
os.environ["GEMINI_API_KEY"] = "dummy_for_testing"

from manimgen.renderer.assembler import assemble_video
from manimgen.planner.lesson_planner import research_topic

def make_dummy_clip(color: str, output_name: str) -> str:
    """Generate a quick 1-second 60fps clip with color background to simulate ManimGL render chunk via FFmpeg directly."""
    out_path = os.path.join(".", output_name)
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=1920x1080:r=60:d=1", 
        "-f", "lavfi", "-i", "anullsrc", "-c:v", "libx264", "-c:a", "aac", "-shortest", out_path
    ], check=True, capture_output=True)
    return out_path

def test_assembler():
    print("=== Testing High-Fidelity Assembler ===")
    try:
        print("Generating mock scenes...")
        clip1 = make_dummy_clip("red", "clip_01_cue00.mp4")
        clip2 = make_dummy_clip("blue", "clip_02_cue00.mp4")
        clip3 = make_dummy_clip("green", "clip_03_cue00.mp4")

        print("Triggering Assembler (This will use the new -preset slow -crf 17 flags) ...")
        # Ensure our tests folder exists first
        os.makedirs(os.path.join("manimgen", "output", "videos"), exist_ok=True)
        final_video_path = assemble_video([clip1, clip2, clip3], "Antigravity_Smoke_Test")
        
        if os.path.exists(final_video_path):
            size = os.path.getsize(final_video_path)
            print(f"[SUCCESS] High-fidelity video generated at: {final_video_path}")
            print(f"[INFO] Size: {size / 1024:.2f} KB (larger correlates to higher fidelity)")
        else:
            print("[ERROR] Final output video missing!")
            
        # Cleanup
        os.remove(clip1)
        os.remove(clip2)
        os.remove(clip3)
        return final_video_path
        
    except Exception as e:
        print(f"[CRITICAL ERROR] Assembler failed: {e}")
        import traceback
        traceback.print_exc()

def test_researcher():
    print("\n=== Testing Expanded Knowledge Panel ===")
    try:
        # It relies on LLM, so if it fails due to auth it's fine, we catch it
        brief = research_topic("Turing Machines")
        if not brief:
            print("Did not generate a brief (Missing/Bad API Key or rate limit), but syntactically correct.")
        else:
            print("[SUCCESS] Brief generated!")
            # Validate new strict schema fields exist
            print(f"Historical Context extracted: {'historical_context' in brief}")
            print(f"Textbook vs Intuition extracted: {'textbook_vs_intuition' in brief}")
            print(f"Multiple Perspectives extracted: {'multiple_perspectives' in brief}")
            
    except Exception as e:
        import traceback
        print(f"[CRITICAL ERROR] Researcher parsing failed: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    final = test_assembler()
    test_researcher()
    if final:
        print(f"\nYour generated test video is located at: {final}")
