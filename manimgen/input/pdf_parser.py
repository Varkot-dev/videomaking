import re
import sys
import warnings


def _clean_text(text: str) -> str:
    """Remove common PDF artifacts and normalize whitespace."""
    # Fix hyphenation breaks (word- \nnext → wordnext)
    text = re.sub(r"-\n(\w)", r"\1", text)

    # Collapse runs of whitespace within lines but preserve paragraph breaks
    lines = text.split("\n")
    cleaned_lines = []
    for line in lines:
        line = re.sub(r"[ \t]+", " ", line).strip()
        cleaned_lines.append(line)
    text = "\n".join(cleaned_lines)

    # Remove isolated page numbers: lines that are just digits (possibly with whitespace)
    text = re.sub(r"(?m)^\s*\d{1,4}\s*$", "", text)

    # Collapse 3+ consecutive blank lines into two (preserve paragraph structure)
    text = re.sub(r"\n{3,}", "\n\n", text)

    return text.strip()


def _looks_like_heading(line: str) -> bool:
    """Heuristic: short line, not ending in a sentence terminator, possibly numbered."""
    line = line.strip()
    if not line:
        return False
    if len(line) > 120:
        return False
    # Numbered headings: "1.", "1.2", "Chapter 3", "Section 4.1"
    if re.match(r"^(\d+\.)+\s+\S", line):
        return True
    if re.match(r"^(Chapter|Section|Part|Appendix)\s+\d", line, re.IGNORECASE):
        return True
    # All-caps short line
    if line.isupper() and 3 < len(line) < 80:
        return True
    # Short line not ending with sentence-ender, and no lowercase beginning with article
    if len(line) < 60 and not line.endswith((".", ",", ";", ":")):
        words = line.split()
        if len(words) >= 2:
            return True
    return False


def _chunk_by_headings(text: str) -> list:
    """Split text into chunks at detected headings."""
    lines = text.split("\n")
    chunks = []
    current_lines = []

    for line in lines:
        if _looks_like_heading(line) and current_lines:
            chunk = "\n".join(current_lines).strip()
            if chunk:
                chunks.append(chunk)
            current_lines = [line]
        else:
            current_lines.append(line)

    # Last chunk
    if current_lines:
        chunk = "\n".join(current_lines).strip()
        if chunk:
            chunks.append(chunk)

    return chunks


def _chunk_by_paragraphs(text: str, min_chars: int = 200) -> list:
    """Fall back: split by blank lines, merging short fragments."""
    raw_paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    buffer = ""

    for para in raw_paragraphs:
        if buffer:
            buffer += "\n\n" + para
        else:
            buffer = para

        if len(buffer) >= min_chars:
            chunks.append(buffer)
            buffer = ""

    if buffer:
        chunks.append(buffer)

    return chunks


def parse_pdf(pdf_path: str) -> dict:
    """
    Extract and structure text from a PDF file.

    Returns:
        {
            "raw_text": str,        full cleaned text
            "chunks": [str],        logical text segments
            "extracted_pages": int  number of pages successfully parsed
        }

    Image-only pages are skipped with a warning printed to stderr.
    """
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ImportError(
            "pypdf is required for PDF parsing. Install it with: pip install pypdf"
        )

    reader = PdfReader(pdf_path)
    total_pages = len(reader.pages)
    page_texts = []
    skipped = 0

    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text() or ""
        except Exception as exc:
            warnings.warn(f"[pdf_parser] Could not extract text from page {i + 1}: {exc}")
            text = ""

        if not text.strip():
            warnings.warn(
                f"[pdf_parser] Page {i + 1} appears to be image-only or empty — skipping."
            )
            skipped += 1
            continue

        page_texts.append(text)

    extracted_pages = total_pages - skipped

    if not page_texts:
        warnings.warn("[pdf_parser] No text could be extracted from any page.")
        return {"raw_text": "", "chunks": [], "extracted_pages": 0}

    # Join all pages with a separator so cross-page context is preserved
    joined = "\n\n".join(page_texts)
    raw_text = _clean_text(joined)

    # Try heading-based chunking first; fall back to paragraph chunking
    chunks = _chunk_by_headings(raw_text)
    if len(chunks) < 3:
        chunks = _chunk_by_paragraphs(raw_text)

    # Final guard: drop empty/trivial chunks
    chunks = [c for c in chunks if len(c.strip()) > 30]

    return {
        "raw_text": raw_text,
        "chunks": chunks,
        "extracted_pages": extracted_pages,
    }


if __name__ == "__main__":
    import json

    if len(sys.argv) < 2:
        print("Usage: python pdf_parser.py <path/to/file.pdf>")
        sys.exit(1)

    result = parse_pdf(sys.argv[1])
    print(f"Extracted pages : {result['extracted_pages']}")
    print(f"Total chunks    : {len(result['chunks'])}")
    print(f"Raw text length : {len(result['raw_text'])} chars")
    print("\n--- First chunk preview ---")
    print(result["chunks"][0][:500] if result["chunks"] else "(none)")
