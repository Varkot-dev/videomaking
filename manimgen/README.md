# ManimGen

The full documentation lives at the repository root: **[../README.md](../README.md)**.

That is the file GitHub renders on the project page, so it is the one that gets
read and the one kept current.

This package previously carried a second, near-duplicate README. The two
drifted — they published different test counts, and neither matched reality —
and, worse, the copy being maintained was the one nobody saw.
`tests/test_docs_accuracy.py` now checks the root document too, so that class of
drift fails the build instead of sitting unnoticed.

## Quick reference

```bash
python3 -m pytest -q          # full suite, fully mocked, no API key needed
manimgen "binary search"      # generate a video from a topic
manimgen --pdf lecture.pdf    # or from a PDF
```

See [../README.md](../README.md) for the pipeline architecture, the Codeguard
repair harness, configuration, and known limitations.
