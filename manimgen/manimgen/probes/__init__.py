"""Code that runs inside the manimgl render child, plus its host-side reader.

``overlap_probe.py`` and ``bootstrap/sitecustomize.py`` are loaded in the
render child (see ``overlap_report.with_probe_env``); ``overlap_report.py`` is
the pipeline-side half that sets the child up and reads the result.
"""
