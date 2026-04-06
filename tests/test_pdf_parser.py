"""
Tests for manimgen/input/pdf_parser.py

Tests text cleaning, chunking logic, and output structure.
Uses real pypdf parsing on a synthetic in-memory PDF.
"""

import io
import pytest

try:
    import pypdf
    HAS_PYPDF = True
except ImportError:
    HAS_PYPDF = False

from manimgen.input.pdf_parser import (
    _clean_text,
    _chunk_by_headings,
    _chunk_by_paragraphs,
)


# ── _clean_text ───────────────────────────────────────────────────────────────

class TestCleanText:

    def test_fixes_hyphenation(self):
        text = "algo-\nrithm is fast"
        result = _clean_text(text)
        assert "algorithm" in result
        assert "-\n" not in result

    def test_removes_isolated_page_numbers(self):
        text = "some content\n42\nmore content"
        result = _clean_text(text)
        assert "\n42\n" not in result

    def test_collapses_blank_lines(self):
        text = "line one\n\n\n\nline two"
        result = _clean_text(text)
        assert "\n\n\n" not in result

    def test_strips_whitespace(self):
        text = "  hello world  "
        result = _clean_text(text)
        assert result == result.strip()

    def test_preserves_content(self):
        text = "Binary search runs in O(log n) time."
        result = _clean_text(text)
        assert "Binary search" in result
        assert "O(log n)" in result

    def test_empty_string(self):
        assert _clean_text("") == ""


# ── _chunk_by_headings ────────────────────────────────────────────────────────

class TestChunkByHeadings:

    def test_detects_numbered_headings(self):
        text = "1. Introduction\nSome intro text.\n2. Methods\nMethod details."
        chunks = _chunk_by_headings(text)
        assert len(chunks) >= 2

    def test_detects_section_prefix(self):
        text = "Section 1: Overview\nContent here.\nSection 2: Details\nMore content."
        chunks = _chunk_by_headings(text)
        assert len(chunks) >= 2

    def test_returns_list_of_strings(self):
        text = "1. Intro\nSome text.\n2. Body\nMore text."
        chunks = _chunk_by_headings(text)
        assert isinstance(chunks, list)
        assert all(isinstance(c, str) for c in chunks)

    def test_no_headings_returns_empty_or_single(self):
        text = "Just a paragraph with no headings at all."
        chunks = _chunk_by_headings(text)
        assert isinstance(chunks, list)

    def test_chunks_not_empty_strings(self):
        text = "1. Intro\nSome text.\n2. Body\nMore text."
        chunks = _chunk_by_headings(text)
        assert all(c.strip() for c in chunks)


# ── _chunk_by_paragraphs ──────────────────────────────────────────────────────

class TestChunkByParagraphs:

    def test_merges_short_paragraphs_below_min_chars(self):
        # Short paragraphs (< 200 chars) are merged into one chunk
        text = "Paragraph one is here.\n\nParagraph two is here.\n\nParagraph three."
        chunks = _chunk_by_paragraphs(text)
        assert len(chunks) == 1
        assert "Paragraph one" in chunks[0]
        assert "Paragraph two" in chunks[0]

    def test_splits_long_paragraphs(self):
        # Paragraphs that together exceed min_chars=200 should produce multiple chunks
        long_para = "x" * 210
        text = f"{long_para}\n\n{long_para}"
        chunks = _chunk_by_paragraphs(text)
        assert len(chunks) == 2

    def test_returns_non_empty_strings(self):
        text = "Hello world.\n\nGoodbye world."
        chunks = _chunk_by_paragraphs(text)
        assert len(chunks) >= 1
        assert all(c.strip() for c in chunks)

    def test_single_paragraph(self):
        text = "Just one paragraph with no blank lines."
        chunks = _chunk_by_paragraphs(text)
        assert len(chunks) == 1

    def test_custom_min_chars(self):
        # With min_chars=10 every paragraph of >10 chars becomes its own chunk
        text = "First paragraph here.\n\nSecond paragraph here.\n\nThird paragraph."
        chunks = _chunk_by_paragraphs(text, min_chars=10)
        assert len(chunks) >= 2


# ── parse_pdf output structure ────────────────────────────────────────────────

@pytest.mark.skipif(not HAS_PYPDF, reason="pypdf not installed")
class TestParsePdfStructure:

    def _make_minimal_pdf(self, text: str) -> str:
        """Write a minimal single-page PDF to /tmp and return the path."""
        import tempfile
        from pypdf import PdfWriter

        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)

        path = tempfile.mktemp(suffix=".pdf")
        with open(path, "wb") as f:
            writer.write(f)
        return path

    def test_returns_required_keys(self):
        from manimgen.input.pdf_parser import parse_pdf
        path = self._make_minimal_pdf("Binary search is fast.")
        result = parse_pdf(path)
        assert "raw_text" in result
        assert "chunks" in result
        assert "extracted_pages" in result

    def test_extracted_pages_is_int(self):
        from manimgen.input.pdf_parser import parse_pdf
        path = self._make_minimal_pdf("Some text.")
        result = parse_pdf(path)
        assert isinstance(result["extracted_pages"], int)

    def test_chunks_is_list(self):
        from manimgen.input.pdf_parser import parse_pdf
        path = self._make_minimal_pdf("Some text.")
        result = parse_pdf(path)
        assert isinstance(result["chunks"], list)

    def test_nonexistent_file_raises(self):
        from manimgen.input.pdf_parser import parse_pdf
        with pytest.raises(Exception):
            parse_pdf("/tmp/definitely_does_not_exist_12345.pdf")
