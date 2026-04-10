import os
import json
import sys
from unittest.mock import MagicMock

from manimgen.cli import main
from manimgen import paths

def test_full_pipeline_success(mocker, tmp_path):
    """
    E2E integration test for the full ManimGen pipeline.
    Mocks LLMs and heavy subprocesses (manimgl, ffmpeg, tts) 
    to ensure the entire orchestrator connects properly.
    """
    # 1. Isolate output directories to tmp_path
    mocker.patch('manimgen.paths.base_dir', return_value=str(tmp_path))
    # Change working directory so that 'videos/' created by our mock gets placed here naturally
    mocker.patch('os.getcwd', return_value=str(tmp_path))
    # If the app relies on relative paths like "videos" it can be safe to just chdir
    original_cwd = os.getcwd()
    os.chdir(str(tmp_path))

    try:
        # 2. Mock TTS to avoid network and slow generation
        def fake_tts(narration, audio_path):
            os.makedirs(os.path.dirname(audio_path), exist_ok=True)
            with open(audio_path, 'wb') as f:
                f.write(b"fake audio mp3 data")
            # Return words for two cues
            timestamps = [
                {"word": "Let's", "start": 0.0, "end": 0.5},
                {"word": "begin.", "start": 0.6, "end": 1.0},
                
                {"word": "Now", "start": 1.5, "end": 2.0},
                {"word": "scan.", "start": 2.1, "end": 2.5}
            ]
            return audio_path, timestamps

        mocker.patch('manimgen.cli._run_tts_for_section', side_effect=lambda sec, idx: fake_tts(sec.get("narration", ""), os.path.join(str(tmp_path), f"audio_{idx}.mp3"))[:2] + (3.0,))

        # 3. Mock Planner LLM
        mock_plan = {
            "title": "Sweep Highlight Test",
            "sections": [
                {
                    "id": "section_01",
                    "title": "Linear Search",
                    "narration": "Let's begin. [CUE] Now scan.",
                    "cue_word_indices": [0, 2],
                    "cues": [
                        {"index": 0, "visual": "Technique: stagger_reveal array"},
                        {"index": 1, "visual": "Technique: sweep_highlight scan_rect over array"}
                    ]
                }
            ]
        }
        mocker.patch('manimgen.cli.plan_lesson', return_value=mock_plan)

        # 4. Mock Director LLM (returns code using .become() for the sweep highlight)
        fake_scene_code = '''
from manimlib import *

class Section01Scene(ThreeDScene):
    def construct(self):
        text = Text("Array")
        self.play(Write(text), run_time=1.0)
        self.wait(1.0)

        # CUE 1
        scan_rect = SurroundingRectangle(text)
        self.play(ShowCreation(scan_rect))
        self.play(scan_rect.animate.become(SurroundingRectangle(text).shift(RIGHT)))
        self.wait(1.0)
'''
        # We patch manimgen.generator.scene_generator.chat because generate_scenes calls chat()
        mocker.patch('manimgen.generator.scene_generator.chat', return_value=fake_scene_code)

        # 5. Mock subprocess.run for manimgl and ffmpeg
        def fake_subprocess_run(args, **kwargs):
            res = MagicMock()
            res.returncode = 0
            res.stdout = "mock stdout"
            res.stderr = "mock stderr"
            
            args_list = list(args)
            if not args_list: 
                return res
            
            cmd = args_list[0]
            
            if cmd == "manimgl":
                # Create fake manimgl video output
                class_name = args_list[2]
                output_path = os.path.join(str(tmp_path), "videos", f"{class_name}.mp4")
                os.makedirs(os.path.dirname(output_path), exist_ok=True)
                with open(output_path, "w") as f:
                    f.write("mock video")
                    
            elif cmd == "ffmpeg":
                # Create fake ffmpeg output
                output_path = args_list[-1]
                if output_path.endswith(".mp4") or output_path.endswith(".m4a"):
                    os.makedirs(os.path.dirname(output_path), exist_ok=True)
                    with open(output_path, "w") as f:
                        f.write("mock ffmpeg output")
                        
            return res

        mocker.patch('subprocess.run', side_effect=fake_subprocess_run)

        # Allow TTS inside main loop
        mocker.patch('manimgen.cli._tts_enabled', return_value=True)

        # Execute
        sys.argv = ["manimgen", "Linear Search"]
        main()

        # 6. Verify assembler created the final movie
        final_video_name = "Sweep_Highlight_Test.mp4"
        final_video_path = os.path.join("output", "videos", final_video_name)
        assert os.path.exists(final_video_path), f"Final assembled video {final_video_path} was not created"
    
    finally:
        os.chdir(original_cwd)
