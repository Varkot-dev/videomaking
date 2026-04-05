import os
import subprocess


def assemble_video(video_paths: list[str], title: str) -> str:
    """Concatenate rendered scene videos into a single final video using FFmpeg."""
    videos_dir = "manimgen/output/videos"
    os.makedirs(videos_dir, exist_ok=True)

    safe_title = title.lower().replace(" ", "_").replace("/", "-")
    output_path = os.path.join(videos_dir, f"{safe_title}.mp4")

    if len(video_paths) == 1:
        os.rename(video_paths[0], output_path)
        return output_path

    # Write concat list for FFmpeg
    list_path = os.path.join(videos_dir, "concat_list.txt")
    with open(list_path, "w") as f:
        for path in video_paths:
            abs_path = os.path.abspath(path)
            f.write(f"file '{abs_path}'\n")

    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "concat",
            "-safe", "0",
            "-i", list_path,
            "-c", "copy",
            output_path,
        ],
        check=True,
    )

    os.remove(list_path)
    return output_path
