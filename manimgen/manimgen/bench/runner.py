"""Plan each topic once (cached per slug), then render a variant of it."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

from manimgen import config as cfg_mod
from manimgen.bench import RUNS_DIR

_PLAN_SNIPPET = """
import sys
from manimgen import cli, paths
from manimgen.planner import lesson_planner as lp
lp.set_fast(True)
paths.set_video_format("short")
plan = lp.plan_short(sys.argv[1], archetype=sys.argv[2])
plan["_topic_hash"] = cli._topic_hash("bench-" + sys.argv[1])
cli._save_plan(plan)
print("planned", plan["title"])
"""


def write_config(root: Path, source: Path | None = None) -> Path:
    """Write a config.yaml with TTS off and every output folder under ``root``."""
    source = source or cfg_mod.config_path()
    cfg = yaml.safe_load(source.read_text(encoding="utf-8"))
    cfg.setdefault("tts", {})["enabled"] = False
    out = dict(cfg_mod.DEFAULTS["output"])
    out.update(cfg.get("output") or {})
    cfg["output"] = {
        k: str(root / v.split("manimgen/output/")[-1]) for k, v in out.items()
    }
    # The voice profile path is relative to config.yaml, which is moving.
    shorts = cfg.setdefault("shorts", {})
    vp = shorts.get("voice_profile")
    if vp and not os.path.isabs(vp):
        shorts["voice_profile"] = str((source.parent / vp).resolve())
    root.mkdir(parents=True, exist_ok=True)
    path = root / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def _prefix() -> list[str]:
    """xvfb-run only on Linux when it is installed."""
    if sys.platform.startswith("linux") and shutil.which("xvfb-run"):
        return ["xvfb-run", "-a"]
    return []


def plan_topic(topic: dict, runs_dir: Path = RUNS_DIR, force: bool = False) -> Path:
    """Plan once per slug in fast mode; returns the cached plan.json."""
    root = runs_dir / "_plans" / topic["slug"]
    plan = root / "plan.json"
    if plan.exists() and not force:
        return plan
    cfg = write_config(root)
    env = dict(os.environ, MANIMGEN_CONFIG=str(cfg))
    with open(root / "plan.log", "w", encoding="utf-8") as log:
        subprocess.run(
            [
                *_prefix(),
                sys.executable,
                "-c",
                _PLAN_SNIPPET,
                topic["topic"],
                topic["format"],
            ],
            cwd=root,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=600,
            check=False,
        )
    if not plan.exists():
        raise RuntimeError(f"planning {topic['slug']} failed, see {root / 'plan.log'}")
    return plan


def run_topic(
    topic: dict, variant: str, env_overrides: dict[str, str], runs_dir: Path = RUNS_DIR
) -> Path:
    """Render the cached plan for one topic under one variant."""
    plan = plan_topic(topic, runs_dir)
    d = runs_dir / variant / topic["slug"]
    cfg = write_config(d)
    shutil.copy(plan, d / "plan.json")
    env = dict(os.environ, **env_overrides, MANIMGEN_CONFIG=str(cfg))
    start = time.time()
    with open(d / "run.log", "w", encoding="utf-8") as log:
        proc = subprocess.run(
            [*_prefix(), sys.executable, "-m", "manimgen.cli", "--resume"],
            cwd=d,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
        )
        log.write(f"exit={proc.returncode}\n")
    (d / "wall").write_text(str(int(time.time() - start)), encoding="utf-8")
    return d


def run_all(
    topics: list[dict],
    variant: str,
    env_overrides: dict[str, str],
    jobs: int = 1,
    runs_dir: Path = RUNS_DIR,
) -> list[Path]:
    # Plan serially first so variants and parallel jobs share one plan per slug.
    for t in topics:
        plan_topic(t, runs_dir)
    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        futures = [
            pool.submit(run_topic, t, variant, env_overrides, runs_dir) for t in topics
        ]
        return [f.result() for f in futures]
