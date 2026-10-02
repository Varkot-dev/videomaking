import os

from setuptools import find_packages, setup

HERE = os.path.dirname(os.path.abspath(__file__))


def _read_requirements(name: str) -> list[str]:
    """Plain requirement lines from a requirements file (comments dropped).

    requirements.txt is the single source of truth for runtime deps, so a
    `pip install -e .` resolves exactly what `pip install -r` would. Environment
    markers such as `; python_version >= "3.13"` pass through untouched.
    """
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        lines = (line.split(" #", 1)[0].strip() for line in f)
        return [line for line in lines if line and not line.startswith(("#", "-"))]


# Layout: this file lives in videomaking/manimgen/, and the importable package
# is videomaking/manimgen/manimgen/. So the package root is *this* directory and
# find_packages() must be called from here.
#
# The previous configuration set package_dir={"": "manimgen"}, which declared
# that packages live *inside* manimgen/. setuptools then installed the
# subpackages as top level — top_level.txt read "editor, generator, input,
# planner, renderer, validator" — while `manimgen` itself was never installed at
# all. Two consequences:
#
#   1. `manimgen --help` died with ModuleNotFoundError on a clean install, even
#      though `import manimgen` appeared to work. The editable .pth pointed one
#      directory too deep, so `import manimgen` resolved to an empty namespace
#      package with __file__ = None and only failed on the first submodule
#      access. Development never caught it because running from inside
#      manimgen/ puts the correct directory on sys.path anyway and shadows the
#      broken entry.
#   2. It squatted generic names — `input`, `editor`, `planner` — in the global
#      namespace of any environment that installed this package.
#
# Verified from a cold clone in a fresh venv: `manimgen --help` now runs.
setup(
    name="manimgen",
    version="0.1.0",
    packages=find_packages(include=["manimgen", "manimgen.*"]),
    python_requires=">=3.11",
    install_requires=_read_requirements("requirements.txt"),
    # Prompts and the editor page are read from next to the modules at runtime
    # (os.path.dirname(__file__)), so they must ship inside the package. They are
    # also listed in MANIFEST.in so an sdist carries them. config.yaml sits one
    # level above the package and is read via "../config.yaml", so it is only
    # available from a source checkout (editable install), not a wheel.
    package_data={
        "manimgen.planner": ["prompts/*.md"],
        "manimgen.generator": ["prompts/*.md"],
        "manimgen.validator": ["prompts/*.md"],
        "manimgen.editor": ["templates/*.html"],
        # Few-shot scenes the Director reads at runtime (scene_generator._examples_dir).
        "manimgen": ["examples/*.py"],
    },
    extras_require={
        # The -r line is skipped, so this is just the dev tooling on top.
        "dev": _read_requirements("requirements-dev.txt"),
    },
    entry_points={
        "console_scripts": [
            "manimgen=manimgen.cli:main",
            "manimgen-edit=manimgen.editor.server:main",
        ],
    },
)
