"""Real dry runs (xvfb on Linux): preflight must see what a real render sees.

Each scene is run twice, once as a real render through ``run_manimgl`` and once
through ``preflight`` (``-s``: the scene is executed without drawing frames).
The probe findings and the kit's own findings must be the same, and the dry run
must be much faster.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
import time

import pytest

from manimgen import paths
from manimgen.validator import render_command
from manimgen.validator.preflight import preflight
from manimgen.validator.retry import SceneErrorType
from tests import test_probe_small_on_shape_render as calib
from tests.kit_scenes import SCENES


def _has_display() -> bool:
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return sys.platform != "win32"


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        importlib.util.find_spec("manimlib") is None
        or shutil.which("manimgl") is None
        or not _has_display(),
        reason="needs manimgl and a display (xvfb-run on Linux)",
    ),
]

# A text that an updater moves a little on every frame into another text. Skip
# mode runs the updater once with the whole dt; the end state is the same.
UPDATER = """from manimlib import *


class Upd(Scene):
    def construct(self):
        a = Text("first words", font_size=28).move_to([-0.3, 1.5, 0])
        b = Text("second words", font_size=28).move_to([-0.3, 0.2, 0])
        b.add_updater(lambda m, dt: m.shift(UP * dt * 0.65))
        self.add(a, b)
        self.play(FadeIn(a), run_time=0.5)
        self.wait(2)
        self.play(a.animate.shift(DOWN * 0.1), run_time=0.5)
"""

CRASH = """from manimlib import *


class Boom(Scene):
    def construct(self):
        t = Text("hello", font_size=28)
        self.add(t)
        self.wait(0.2)
        t.no_such_method()
"""

LOOP_TIME = """from manimlib import *


class Clock(Scene):
    def construct(self):
        t = Text("tick", font_size=28).move_to([-0.3, 1.0, 0])
        self.add(t)
        for _ in range(3):
            self.play(t.animate.shift(RIGHT * 0.1), run_time=0.5)
        self.wait(1.5)
        print("SCENE-TIME", round(self.time, 3))
"""


def _in(tmp_path, short, fn):
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        paths.set_video_format("short" if short else "landscape")
        return fn()
    finally:
        os.chdir(cwd)
        paths.set_video_format("landscape")


def _real(tmp_path, source, cls):
    scene = tmp_path / f"{cls}.py"
    scene.write_text(source, encoding="utf-8")

    def go():
        t = time.monotonic()
        res = render_command.run_manimgl(str(scene), cls, timeout=900)
        return res, time.monotonic() - t

    res, secs = _in(tmp_path, True, go)
    return res, secs, str(scene)


def _dry(tmp_path, scene, cls, short=True):
    return _in(tmp_path, short, lambda: preflight(scene, cls))


def _key(overlaps):
    return sorted((o.kind, o.a, o.b) for o in overlaps)


# The calibration scenes last a fraction of a second, so only the kit scenes
# (several seconds of animation) are held to a time saving.
SCENES_TIMED = {"TriangleScene", "GridScene", "EquationScene"}

CASES = [
    (SCENES["TriangleScene"], "TriangleScene"),
    (SCENES["GridScene"], "GridScene"),
    (SCENES["EquationScene"], "EquationScene"),
    (calib.GOOD1, "Good1"),
    (calib.GOOD2, "Good2"),
    (calib.BAD_ON_SHAPE, "Bad1"),
    (calib.BAD_SMALL, "Bad2"),
    (UPDATER, "Upd"),
]


@pytest.mark.timeout(900)
@pytest.mark.parametrize("source, cls", CASES, ids=[c[1] for c in CASES])
def test_dry_run_sees_the_same_findings_as_a_real_render(tmp_path, source, cls):
    real, real_secs, scene = _real(tmp_path, source, cls)
    assert real.ok, real.stderr
    dry = _dry(tmp_path, scene, cls)
    assert dry.ran and not dry.crashed, dry.stderr
    assert dry.probe_error is None
    assert _key(dry.overlaps) == _key(real.overlaps)
    prefix = "[scenekit] "
    kit_real = {
        ln.strip()[len(prefix) :]
        for ln in real.stdout.splitlines()
        if ln.strip().startswith(prefix)
    }
    assert set(dry.kit_findings) == kit_real
    print(f"TIMES {cls}: real {real_secs:.1f}s, dry {dry.seconds:.1f}s")
    if cls in SCENES_TIMED:
        assert dry.seconds < real_secs / 3
    # The dry run's last-frame PNG goes to a temporary folder that is removed.
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.timeout(300)
def test_scene_time_advances_in_a_dry_run(tmp_path):
    scene = tmp_path / "Clock.py"
    scene.write_text(LOOP_TIME, encoding="utf-8")
    res = _in(
        tmp_path,
        True,
        lambda: render_command.run_manimgl(
            str(scene), "Clock", timeout=300, dry_run=True
        ),
    )
    assert res.ok and res.dry_run and res.video_path is None
    assert "SCENE-TIME 3.0" in res.stdout


@pytest.mark.timeout(300)
def test_a_crash_in_a_dry_run_is_a_crash(tmp_path):
    scene = tmp_path / "Boom.py"
    scene.write_text(CRASH, encoding="utf-8")
    dry = _dry(tmp_path, str(scene), "Boom")
    assert dry.crashed and not dry.ran and not dry.clean
    assert "no_such_method" in dry.stderr
    assert dry.error_type == SceneErrorType.ATTRIBUTE


@pytest.mark.timeout(300)
def test_landscape_dry_run_keeps_landscape_checks(tmp_path):
    scene = tmp_path / "Bad1.py"
    scene.write_text(calib.BAD_ON_SHAPE, encoding="utf-8")
    dry = _dry(tmp_path, str(scene), "Bad1", short=False)
    assert dry.ran
    assert not {o.kind for o in dry.overlaps} & {"on_shape", "small"}


@pytest.mark.timeout(300)
def test_the_safety_gate_still_decides_in_a_dry_run(tmp_path):
    scene = tmp_path / "Evil.py"
    marker = tmp_path / "ran.txt"
    scene.write_text(
        "from manimlib import *\n"
        "import os\n\n\n"
        "class Evil(Scene):\n"
        "    def construct(self):\n"
        f"        open({str(marker)!r}, 'w').write('x')\n"
        "        os.system('true')\n",
        encoding="utf-8",
    )
    res = render_command.run_manimgl(str(scene), "Evil", dry_run=True)
    assert not res.ok and res.returncode is None
    assert not marker.exists()
    dry = _dry(tmp_path, str(scene), "Evil")
    assert dry.crashed
    assert dry.error_type == SceneErrorType.SAFETY_GATE
