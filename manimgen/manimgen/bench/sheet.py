"""One PNG per variant: four frames of every video, so a human can review all topics at once."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

PERCENTS = (5, 35, 65, 95)
_FFMPEG_TIMEOUT = 300


def probe_video(path: Path) -> tuple[float, int, int]:
    """Duration seconds, width, height via ffprobe."""
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout
    data = json.loads(out)
    st = data["streams"][0]
    return float(data["format"]["duration"]), int(st["width"]), int(st["height"])


def tile_size(width: int, height: int) -> tuple[int, int]:
    """270x480 for vertical, 480x270 for landscape."""
    return (270, 480) if height >= width else (480, 270)


def xstack_layout(n: int, cols: int = len(PERCENTS)) -> str:
    """xstack layout string for ``n`` equal tiles in rows of ``cols``."""
    cells = []
    for i in range(n):
        r, c = divmod(i, cols)
        x = "+".join(["w0"] * c) or "0"
        y = "+".join(["h0"] * r) or "0"
        cells.append(f"{x}_{y}")
    return "|".join(cells)


def build_command(
    videos: list[tuple[Path, float]], tile: tuple[int, int], out: Path
) -> list[str]:
    """ffmpeg argv that tiles the frames at PERCENTS of each (video, duration)."""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    n = 0
    for path, dur in videos:
        for pct in PERCENTS:
            cmd += ["-ss", f"{dur * pct / 100:.3f}", "-i", str(path)]
            n += 1
    w, h = tile
    scale = ";".join(
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2[v{i}]"
        for i in range(n)
    )
    labels = "".join(f"[v{i}]" for i in range(n))
    if n == 1:
        graph = f"{scale};[v0]copy[out]"
    else:
        graph = f"{scale};{labels}xstack=inputs={n}:layout={xstack_layout(n)}[out]"
    cmd += ["-filter_complex", graph, "-map", "[out]", "-frames:v", "1", str(out)]
    return cmd


def make_sheet(videos: list[Path], out: Path) -> Path:
    """Write the frame sheet for ``videos`` (one row each) to ``out``."""
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise RuntimeError("ffmpeg and ffprobe are needed for the frame sheet")
    if not videos:
        raise ValueError("no videos to tile")
    info = [(v, *probe_video(v)) for v in videos]
    tile = tile_size(info[0][2], info[0][3])
    cmd = build_command([(v, d) for v, d, _, _ in info], tile, out)
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, timeout=_FFMPEG_TIMEOUT, capture_output=True)
    return out
