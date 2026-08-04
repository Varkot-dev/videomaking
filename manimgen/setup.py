from setuptools import find_packages, setup

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
    entry_points={
        "console_scripts": [
            "manimgen=manimgen.cli:main",
            "manimgen-edit=manimgen.editor.server:main",
        ],
    },
)
