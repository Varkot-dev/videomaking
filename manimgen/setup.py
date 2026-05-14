from setuptools import setup, find_packages

setup(
    name="manimgen",
    version="0.1.0",
    package_dir={"": "manimgen"},
    packages=find_packages("manimgen", exclude=["tests", "tests.*"]),
    entry_points={
        "console_scripts": [
            "manimgen=manimgen.cli:main",
            "manimgen-edit=manimgen.editor.server:main",
        ],
    },
)
