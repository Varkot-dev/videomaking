"""Start-up hook for the manimgl render child (#97).

``render_command.run_manimgl`` puts this directory on the child's PYTHONPATH,
so Python imports this file at start-up (the standard ``sitecustomize``
mechanism; it also works through the Windows ``manimgl.exe`` launcher, which
starts a normal Python interpreter). It does two things:

1. Runs the environment's own ``sitecustomize``, if there is one, because this
   file shadows it (Debian and Ubuntu ship one, for example).
2. When ``MANIMGEN_OVERLAP_REPORT`` is set, loads ``../overlap_probe.py`` by
   path (without importing the manimgen package) and installs it.

Every step is wrapped: a failure here must never stop a render.
"""

import importlib.machinery
import importlib.util
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _run_shadowed_sitecustomize():
    def _is_here(entry):
        try:
            return os.path.normcase(os.path.abspath(entry or os.curdir)) == (
                os.path.normcase(_HERE)
            )
        except Exception:
            return False

    search = [p for p in sys.path if not _is_here(p)]
    spec = importlib.machinery.PathFinder.find_spec("sitecustomize", search)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


def _install_overlap_probe():
    report = os.environ.get("MANIMGEN_OVERLAP_REPORT")
    if not report:
        return
    probe_path = os.path.join(os.path.dirname(_HERE), "overlap_probe.py")
    name = "_manimgen_overlap_probe"
    spec = importlib.util.spec_from_file_location(name, probe_path)
    if spec is None or spec.loader is None:
        return
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        module.install()
    except Exception as exc:
        sys.modules.pop(name, None)
        _write_load_error(report, exc)


def _write_load_error(report, exc):
    try:
        import json

        with open(report, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "version": 1,
                    "findings": [],
                    "probe_error": f"load: {type(exc).__name__}: {exc}"[:300],
                },
                f,
            )
    except Exception:
        pass


try:
    _run_shadowed_sitecustomize()
except Exception:
    pass

try:
    _install_overlap_probe()
except Exception:
    pass
