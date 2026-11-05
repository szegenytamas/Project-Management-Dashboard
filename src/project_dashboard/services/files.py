"""Checking uploaded files before they go anywhere near S3.

Never trust what the client says about a file. Both the file name and the
Content-Type header are chosen by the client and trivially faked - an .exe
renamed to report.pdf arrives with `Content-Type: application/pdf` if the
attacker wants it to. So we decide the type from the file's own first bytes
(its "magic number") and require the extension to agree.
"""

import io
import zipfile
from dataclasses import dataclass
from pathlib import PureWindowsPath

from fastapi import UploadFile

from project_dashboard.services.errors import FileTooLargeError, UnsupportedFileTypeError

PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
EXTENSIONS = {".pdf": PDF, ".docx": DOCX}
MAX_FILENAME_LENGTH = 255


@dataclass(frozen=True)
class ValidatedFile:
    filename: str
    content_type: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def extension(self) -> str:
        return EXTENSIONS_BY_TYPE[self.content_type]


EXTENSIONS_BY_TYPE = {v: k for k, v in EXTENSIONS.items()}


def clean_filename(raw: str | None) -> str:
    """Keep only the last path component: 'C:\\fakepath\\a.pdf' or '../../a.pdf' -> 'a.pdf'.

    PureWindowsPath understands both '/' and '\\' as separators. The name is
    only ever stored as metadata - it is never used as the S3 key or a path.
    """
    name = PureWindowsPath(raw or "").name.strip()
    if len(name) > MAX_FILENAME_LENGTH:
        stem, dot, ext = name.rpartition(".")
        name = stem[: MAX_FILENAME_LENGTH - len(ext) - 1] + dot + ext
    return name


def detect_type(data: bytes) -> str | None:
    """Identify PDF or DOCX from the content itself."""
    if data.startswith(b"%PDF-"):
        return PDF
    # A .docx is a ZIP archive that contains word/document.xml.
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                if "word/document.xml" in archive.namelist():
                    return DOCX
        except zipfile.BadZipFile:
            return None
    return None


async def read_and_validate(upload: UploadFile, max_size: int) -> ValidatedFile:
    """Read one uploaded file (at most max_size bytes) and check its type."""
    filename = clean_filename(upload.filename)
    # Read one byte more than allowed: if we get it, the file is too big -
    # without ever loading an arbitrarily large upload into memory.
    data = await upload.read(max_size + 1)
    if len(data) > max_size:
        raise FileTooLargeError(f"'{filename}' is larger than {max_size // 1024} KB")

    extension = PureWindowsPath(filename).suffix.lower()
    expected = EXTENSIONS.get(extension)
    detected = detect_type(data)
    if expected is None or detected != expected:
        raise UnsupportedFileTypeError(
            f"'{filename or 'unnamed file'}' is not a valid PDF or DOCX document"
        )
    return ValidatedFile(filename=filename, content_type=detected, data=data)
