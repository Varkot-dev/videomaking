from setuptools import setup, find_packages

setup(
    name="manimgen",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "console_scripts": [
            "manimgen=manimgen.cli:main",
            "manimgen-edit=manimgen.editor.server:main",
        ],
    },
)
