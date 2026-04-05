import subprocess
import os
from datetime import datetime
from manimgen.validator.codeguard import precheck_and_autofix
from manimgen.validator.env import get_render_env


def run_scene(scene_path: str, class_name: str) -> tuple[bool, str | None]:
    """
    Run a ManimGL scene file and return (success, video_path).
    Logs the attempt to output/logs/.
    """
    logs_dir = "manimgen/output/logs"
    os.makedirs(logs_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(logs_dir, f"{class_name}_{timestamp}.log")

    precheck = precheck_and_autofix(scene_path)
    if not precheck["ok"]:
        with open(log_path, "w") as f:
            if precheck["applied_fixes"]:
                f.write("=== PRECHECK AUTO-FIXES ===\n")
                for fix in precheck["applied_fixes"]:
                    f.write(f"- {fix}\n")
                f.write("\n")
            f.write("=== PRECHECK ERROR ===\n")
            f.write(precheck["stderr"])
            f.write("\n")
        return False, None

    try:
        result = subprocess.run(
            ["manimgl", scene_path, class_name, "-w", "--hd"],
            capture_output=True,
            text=True,
            timeout=120,
            env=get_render_env(),
        )

        with open(log_path, "w") as f:
            if precheck["applied_fixes"]:
                f.write("=== PRECHECK AUTO-FIXES ===\n")
                for fix in precheck["applied_fixes"]:
                    f.write(f"- {fix}\n")
                f.write("\n")
            f.write(f"=== STDOUT ===\n{result.stdout}\n")
            f.write(f"=== STDERR ===\n{result.stderr}\n")
            f.write(f"=== RETURN CODE ===\n{result.returncode}\n")

        if result.returncode == 0:
            video_path = _find_rendered_video(class_name)
            return True, video_path

        return False, None

    except subprocess.TimeoutExpired:
        with open(log_path, "w") as f:
            f.write("=== TIMEOUT ===\nScene rendering exceeded 120 seconds.\n")
        return False, None


def _find_rendered_video(class_name: str) -> str | None:
    """Search common ManimGL output directories for the rendered video."""
    search_dirs = ["videos", "media/videos"]
    for d in search_dirs:
        for root, _, files in os.walk(d):
            for f in files:
                if class_name in f and f.endswith(".mp4"):
                    return os.path.join(root, f)
    return None
