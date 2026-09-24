"""Safe filename headers shared by HTTP downloads."""

from urllib.parse import quote

from aiohttp import web

from gideon.security.security import redact_field


def download_headers(filename: str, *, inline: bool = False) -> dict[str, str]:
    """Redact a filename and emit ASCII fallback plus RFC 6266 UTF-8 metadata."""
    if any(ord(char) < 32 or 127 <= ord(char) <= 159 for char in filename):
        raise web.HTTPBadRequest(text="Invalid download filename")
    safe = redact_field(filename)
    safe = safe.replace("\\", "_").replace("/", "_").strip(" .") or "download"
    fallback = "".join(
        char if char.isascii() and (char.isalnum() or char in " ._-") else "_"
        for char in safe
    )
    disposition = "inline" if inline else "attachment"
    return {
        "Content-Disposition": (
            f'{disposition}; filename="{fallback}"; '
            f"filename*=UTF-8''{quote(safe, safe='')}"
        ),
        "X-Content-Type-Options": "nosniff",
    }
