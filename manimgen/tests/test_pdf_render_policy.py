"""Page rendering policy of input/pdf_parser.py (CLAUDE.md known issue #7).

parse_pdf used to render EVERY page to a base64 PNG, so a text-heavy PDF sent
the planner up to 50 redundant page images. A page is now rendered only when
its text cannot carry it:

  1. no extractable text (scanned page)       -> render
  2. text plus raster images or diagrams       -> render
  3. text only                                  -> skip

The PDFs here are built with PyMuPDF itself, so no fixture files are needed.
"""

import warnings

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("pypdf")

from manimgen.input import pdf_parser  # noqa: E402

_PARAGRAPH = (
    "Binary search halves the search interval on every comparison, "
    "so a sorted array of n items needs about log2 n steps."
)


def _tiny_png() -> bytes:
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 8, 8), False)
    pix.clear_with(200)
    return pix.tobytes("png")


def _build_pdf(path, kinds):
    """Write a PDF with one page per entry of kinds.

    text      -> text only
    image     -> text plus an embedded raster image
    diagram   -> text plus many vector drawing operations
    rule      -> text plus a single horizontal line (not a diagram)
    blank     -> nothing at all (stands in for a scanned page)
    """
    doc = fitz.open()
    for kind in kinds:
        page = doc.new_page(width=612, height=792)
        if kind != "blank":
            page.insert_text((72, 72), _PARAGRAPH, fontsize=10)
        if kind == "image":
            page.insert_image(fitz.Rect(72, 200, 272, 400), stream=_tiny_png())
        elif kind == "diagram":
            for i in range(pdf_parser._MIN_DRAWINGS_FOR_VISUAL + 5):
                y = 200 + 10 * i
                page.draw_line((72, y), (300, y + 5))
        elif kind == "rule":
            page.draw_line((72, 90), (540, 90))
    doc.save(str(path))
    doc.close()
    return str(path)


def _parse(path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return pdf_parser.parse_pdf(path)


@pytest.mark.parametrize(
    "has_text,has_visuals,expected",
    [
        (False, False, True),  # scanned page: the image is all there is
        (False, True, True),
        (True, True, True),  # text plus diagram
        (True, False, False),  # text only: skip
    ],
)
def test_should_render_page_three_way(has_text, has_visuals, expected):
    assert pdf_parser._should_render_page(has_text, has_visuals) is expected


def test_text_only_pdf_renders_no_pages(tmp_path):
    result = _parse(_build_pdf(tmp_path / "t.pdf", ["text", "text", "rule"]))
    assert result["images"] == []
    assert result["extracted_pages"] == 3
    assert "Binary search" in result["raw_text"]


def test_only_pages_needing_an_image_are_rendered(tmp_path, monkeypatch):
    rendered = []
    real = pdf_parser._render_page_to_b64

    def spy(doc, page_index, dpi=150):
        rendered.append(page_index)
        return real(doc, page_index, dpi)

    monkeypatch.setattr(pdf_parser, "_render_page_to_b64", spy)
    kinds = ["text", "image", "text", "diagram", "blank", "rule"]
    result = _parse(_build_pdf(tmp_path / "m.pdf", kinds))
    assert rendered == [1, 3, 4]
    assert len(result["images"]) == 3
    assert result["extracted_pages"] == 5  # the blank page has no text


def test_scanned_only_pdf_still_returns_page_images(tmp_path):
    result = _parse(_build_pdf(tmp_path / "s.pdf", ["blank", "blank"]))
    assert result["raw_text"] == ""
    assert len(result["images"]) == 2


def test_visual_probe_failure_errs_toward_rendering():
    class Broken:
        def get_images(self, full=False):
            raise RuntimeError("malformed page")

    assert pdf_parser._page_has_visuals(Broken()) is True
