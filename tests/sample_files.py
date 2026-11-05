"""Tiny but structurally valid PDF and DOCX files for upload tests."""

import io
import zipfile

PDF_TYPE = "application/pdf"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def make_pdf(size: int = 1_000, marker: str = "") -> bytes:
    """Starts with the %PDF- magic number, padded to roughly `size` bytes."""
    head = f"%PDF-1.7\n% {marker}\n".encode()
    tail = b"\n%%EOF\n"
    return head + b"0" * max(0, size - len(head) - len(tail)) + tail


def make_docx(marker: str = "hello") -> bytes:
    """A ZIP archive containing word/document.xml - what makes it a DOCX."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", f"<w:document>{marker}</w:document>")
    return buffer.getvalue()


def make_zip_without_word() -> bytes:
    """A ZIP that is NOT a Word document (e.g. a renamed archive)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "just a zip")
    return buffer.getvalue()
