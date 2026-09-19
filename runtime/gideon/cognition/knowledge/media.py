"""Media kind selection and best-effort image previews."""

import mimetypes
from pathlib import Path
from types import ModuleType

Image: ModuleType | None
try:
    from PIL import Image as _PillowImage
except ImportError:
    Image = None
else:
    Image = _PillowImage

_EXT_TYPE: dict[str, str] = {
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".gif": "image",
    ".webp": "image",
    ".bmp": "image",
    ".svg": "image",
    ".heic": "image",
    ".heif": "image",
    ".tiff": "image",
    ".tif": "image",
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".mp4": "video",
    ".mov": "video",
    ".avi": "video",
    ".mkv": "video",
    ".webm": "video",
    ".m4v": "video",
    ".pdf": "pdf",
    ".docx": "document",
    ".doc": "document",
    ".xlsx": "sheet",
    ".xls": "sheet",
    ".csv": "sheet",
    ".tsv": "sheet",
    ".pptx": "slides",
    ".ppt": "slides",
    ".md": "document",
    ".markdown": "document",
    ".txt": "document",
    ".text": "document",
    ".rst": "document",
    ".html": "document",
    ".htm": "document",
    ".log": "document",
}

_CODE_EXT_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".rb": "ruby",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".sql": "sql",
    ".css": "css",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
}

BINARY_MEDIA_TYPES = {"image", "audio", "video"}

_THUMBNAIL_MAX = (480, 480)

_CANONICAL_MIME: dict[str, str] = {
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".m4v": "video/mp4",
}


class _MediaName:
    def __init__(self, filename):
        self.filename = filename
        self.extension = Path(filename).suffix.lower()

    def kind(self, mime):
        selected = _EXT_TYPE.get(self.extension)
        hint = (mime or "").partition("/")[0].lower()
        if selected in BINARY_MEDIA_TYPES and hint in BINARY_MEDIA_TYPES:
            return hint
        return (
            selected
            if selected is not None
            else ("gist" if self.extension in _CODE_EXT_LANGUAGE else None)
        )

    def mime(self):
        canonical = _CANONICAL_MIME.get(self.extension)
        return (
            canonical
            or mimetypes.guess_type(self.filename)[0]
            or "application/octet-stream"
        )


def code_language(filename: str) -> str | None:
    return _CODE_EXT_LANGUAGE.get(_MediaName(filename).extension)


def classify(filename: str, mime: str | None = None) -> str | None:
    return _MediaName(filename).kind(mime)


def is_binary_media(filename: str) -> bool:
    return _MediaName(filename).kind(None) in BINARY_MEDIA_TYPES


def guess_mime(filename: str) -> str:
    return _MediaName(filename).mime()


def make_image_thumbnail(src_path: str, dest_path: str) -> bool:
    image = Image
    if image is None or _MediaName(src_path).extension == ".svg":
        return False
    try:
        with image.open(src_path) as source:
            preview = source.convert("RGB")
            try:
                preview.thumbnail(_THUMBNAIL_MAX)
                preview.save(dest_path, format="WEBP", quality=80)
            finally:
                preview.close()
    except Exception:
        return False
    return True
