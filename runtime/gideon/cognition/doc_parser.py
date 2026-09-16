"""Bounded Office XML readers and a dependency-free PDF text scanner."""

import logging
import re
import xml.etree.ElementTree as ETree
import zipfile
import zlib
from pathlib import Path

from gideon.security.security import is_sensitive_path
from gideon.security.sel import sel

logger = logging.getLogger(__name__)
_MAX_ZIP_ENTRY = 50 * 1024 * 1024
_MAX_DECOMPRESS = 50 * 1024 * 1024
DOC_MIMETYPES: dict[str, str] = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/pdf": "pdf",
    "application/msword": "docx",
    "application/vnd.ms-powerpoint": "pptx",
}
DOC_EXTENSIONS: set[str] = {".docx", ".pdf", ".pptx"}
_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_A_NS = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
_SLIDE_RE = re.compile(r"^ppt/slides/slide(\d+)\.xml$")
_PDF_TEXT_RE = re.compile(rb"\(([^)]*)\)")
_PDF_STREAM_RE = re.compile(rb"stream\r?\n(.*?)endstream", re.DOTALL)
_EXT_FORMAT = {".docx": "docx", ".pptx": "pptx", ".pdf": "pdf"}


def is_parseable_document(mimetype: str = "", filename: str = "") -> bool:
    return mimetype in DOC_MIMETYPES or bool(
        filename and Path(filename).suffix.lower() in DOC_EXTENSIONS
    )


def extract_text(path: str, mimetype: str = "", filename: str = "") -> str:
    if is_sensitive_path(path):
        logger.warning("Refusing to read sensitive path: %s", path)
        denial = dict(
            caller="doc_parser",
            operation="extract_text",
            outcome="denied",
            source="local",
            resources=path,
            error="sensitive_path_rejected",
        )
        sel().log_api_access(**denial)
        return ""
    format_name = DOC_MIMETYPES.get(mimetype, "")
    if not format_name:
        format_name = _EXT_FORMAT.get(Path(filename or path).suffix.lower(), "")
    parsers = {"docx": _extract_docx, "pptx": _extract_pptx, "pdf": _extract_pdf}
    try:
        parser = parsers.get(format_name)
        return parser(path) if parser is not None else ""
    except Exception:
        logger.warning("Failed to extract text from %s", path, exc_info=True)
        return ""


def _safe_decompress(data: bytes, max_size: int | None = None) -> bytes:
    limit = _MAX_DECOMPRESS if max_size is None else max_size
    decoder = zlib.decompressobj()
    output = decoder.decompress(data, limit)
    if not decoder.unconsumed_tail:
        return output
    raise ValueError("decompressed stream exceeds size limit")


class _ArchiveRead:
    def __init__(self, stream, limit):
        self.stream, self.limit = stream, limit

    def collect(self):
        remaining = self.limit + 1
        if remaining <= 0:
            return self.stream.read(remaining)
        blocks = []
        while remaining:
            block = self.stream.read(min(65_536, remaining))
            if not block:
                break
            blocks.append(block)
            remaining -= len(block)
        return b"".join(blocks)


def _read_zip_entry(
    zf: zipfile.ZipFile, name: str, max_size: int | None = None
) -> bytes | None:
    limit = _MAX_ZIP_ENTRY if max_size is None else max_size
    with zf.open(name) as stream:
        data = _ArchiveRead(stream, limit).collect()
    if len(data) <= limit:
        return data
    logger.warning("ZIP entry too large (actual): %s", name)
    return None


class _OfficeArchive:
    def __init__(self, archive):
        self.archive = archive

    def tree(self, name):
        payload = _read_zip_entry(self.archive, name)
        return ETree.fromstring(payload) if payload is not None else None

    @staticmethod
    def text_runs(element, namespace):
        return [node.text for node in element.iter(namespace + "t") if node.text]

    def document(self):
        name = "word/document.xml"
        if name not in self.archive.namelist():
            return ""
        root = self.tree(name)
        if root is None:
            return ""
        paragraphs = (
            self.text_runs(element, _W_NS) for element in root.iter(_W_NS + "p")
        )
        return "\n".join("".join(parts) for parts in paragraphs if parts)

    def slides(self):
        entries = []
        for name in self.archive.namelist():
            match = _SLIDE_RE.match(name)
            if match:
                entries.append((int(match.group(1)), name))
        entries.sort(key=lambda entry: entry[0])
        sections = []
        for number, name in entries:
            root = self.tree(name)
            if root is None:
                continue
            runs = self.text_runs(root, _A_NS)
            if runs:
                sections.append(f"--- Slide {number} ---\n" + "\n".join(runs))
        return "\n\n".join(sections)


def _extract_docx(path: str) -> str:
    if is_sensitive_path(path):
        return ""
    with zipfile.ZipFile(path, "r") as archive:
        reader = _OfficeArchive(archive)
        return reader.document()


def _extract_pptx(path: str) -> str:
    if is_sensitive_path(path):
        return ""
    with zipfile.ZipFile(path, "r") as archive:
        reader = _OfficeArchive(archive)
        return reader.slides()


class _PdfTextScan:
    def __init__(self, data, path):
        self.data, self.path = data, path

    def streams(self):
        accepted = 0
        for block in _PDF_STREAM_RE.finditer(self.data):
            payload = block.group(1)
            try:
                payload = _safe_decompress(payload)
            except (zlib.error, OSError):
                pass
            except ValueError:
                logger.warning(
                    "PDF stream exceeded decompression limit in %s", self.path
                )
                continue
            accepted += 1
            yield payload
        if not accepted:
            yield self.data

    def strings(self):
        for payload in self.streams():
            for token in _PDF_TEXT_RE.finditer(payload):
                try:
                    value = token.group(1).decode("utf-8", errors="replace")
                    if len(value) > 1:
                        yield value
                except Exception:
                    continue

    def render(self):
        text = " ".join(self.strings())
        return re.sub(r" {2,}", " ", text).strip()


def _extract_pdf(path: str) -> str:
    if is_sensitive_path(path):
        return ""
    scan = _PdfTextScan(Path(path).read_bytes(), path)
    return scan.render()
