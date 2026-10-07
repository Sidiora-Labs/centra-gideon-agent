"""Validate, resize and encode uploaded images for model image parts."""

from __future__ import annotations

import base64
import io
import logging
import mimetypes
import os
import stat
import warnings
from pathlib import Path

from gideon.interfaces.dashboard.screen_context import ALLOWED_MEDIA_TYPES

logger = logging.getLogger(__name__)

MAX_EDGE_PX = 1568
MAX_PART_BYTES = 3_750_000
_FORMATS = {
    "PNG": ("image/png", {".png"}),
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "WEBP": ("image/webp", {".webp"}),
    "GIF": ("image/png", {".gif"}),
    "BMP": ("image/png", {".bmp"}),
    "TIFF": ("image/png", {".tif", ".tiff"}),
}


def is_image_attachment(path: str) -> bool:
    """Use the shared upload classifier so extraction does not eagerly read images."""
    if not path:
        return False
    from gideon.workspace.uploads.policy import category_for

    return category_for(path, mimetypes.guess_type(path)[0]) == "image"


def image_part_url(path: str) -> str:
    """Return a checked, provider-sized data URL, or an empty string on refusal."""
    if not is_image_attachment(path):
        return ""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode):
                return ""
            from gideon.workspace.uploads.policy import check_upload

            gate = check_upload(
                Path(path).name, mimetypes.guess_type(path)[0], size=info.st_size
            )
            if not gate.ok or gate.category != "image":
                return ""
            raw = stream.read(info.st_size + 1)
            if len(raw) != info.st_size:
                return ""
        media_type, payload = _fit(raw, Path(path).suffix.lower())
    except Exception:
        logger.info("image part refused during byte validation", exc_info=True)
        return ""
    if not payload or media_type not in ALLOWED_MEDIA_TYPES:
        return ""
    return f"data:{media_type};base64,{base64.b64encode(payload).decode('ascii')}"


def _fit(raw: bytes, extension: str) -> tuple[str, bytes]:
    """Return first-frame PNG/JPEG/WebP bytes that fit every supported wire."""
    from PIL import Image

    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(raw)) as image:
            image_format = (image.format or "").upper()
            declared = _FORMATS.get(image_format)
            if declared is None or extension not in declared[1]:
                return "", b""
            media_type = declared[0]
            animated = bool(getattr(image, "is_animated", False))
            if (
                media_type in ALLOWED_MEDIA_TYPES
                and max(image.size) <= MAX_EDGE_PX
                and len(raw) <= MAX_PART_BYTES
                and not animated
            ):
                return media_type, raw
            image.seek(0)
            frame = image.copy()

    frame.thumbnail((MAX_EDGE_PX, MAX_EDGE_PX))
    has_alpha = frame.mode in ("RGBA", "LA") or (
        frame.mode == "P" and "transparency" in frame.info
    )
    if has_alpha or image_format in ("PNG", "GIF", "BMP", "TIFF"):
        png = _encode(frame.convert("RGBA" if has_alpha else "RGB"), "PNG")
        if len(png) <= MAX_PART_BYTES:
            return "image/png", png
    jpeg = _encode(frame.convert("RGB"), "JPEG", quality=85)
    return "image/jpeg", jpeg if len(jpeg) <= MAX_PART_BYTES else b""


def _encode(frame, fmt: str, **kwargs) -> bytes:
    buffer = io.BytesIO()
    frame.save(buffer, format=fmt, **kwargs)
    return buffer.getvalue()
