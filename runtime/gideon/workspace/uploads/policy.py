"""Filetype-keyed upload size policy — one gate for every upload surface.

Before this module every surface hand-rolled its own byte cap (chat attach 50 MB,
knowledge ingest 50 MB, STT 25 MB, workspace 50 MB) under a single 60 MB transport
ceiling, so video — almost always >50 MB — could not be uploaded at all. This is
the single source of truth: a filename/mime maps to one of six **categories** via
the existing :func:`knowledge.media.classify`, each category has a default byte
limit, and every surface calls :func:`check_upload` (streaming, as bytes arrive)
so the per-filetype policy — not the transport — is the real gate.

The transport ceilings (aiohttp ``client_max_size`` on the upload sub-app, nginx
``client_max_body_size``) track :func:`max_category_limit` so the policy decides.

Every limit is overridable via ``GIDEON_UPLOAD_LIMIT_<CATEGORY>`` (bytes),
e.g. ``GIDEON_UPLOAD_LIMIT_VIDEO=3221225472`` for a 3 GB video cap.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from gideon.cognition.knowledge.media import classify

_MB = 1024 * 1024
_GB = 1024 * _MB

UPLOAD_CATEGORIES = ("video", "audio", "image", "document", "archive", "other")

_DEFAULT_LIMITS: dict[str, int] = {
    "video": 2 * _GB,
    "audio": 1 * _GB,
    "image": 200 * _MB,
    "document": 100 * _MB,
    "archive": 500 * _MB,
    "other": 100 * _MB,
}

_SINGLE_POST_THRESHOLD = 50 * _MB

_ARCHIVE_EXT = {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar"}
_KNOWLEDGE_TYPE_TO_CATEGORY = {
    "image": "image",
    "audio": "audio",
    "video": "video",
    "pdf": "document",
    "document": "document",
    "sheet": "document",
    "slides": "document",
    "gist": "document",
}


@dataclass(frozen=True)
class UploadCheck:
    """Result of a size/type policy check. ``ok`` gates acceptance; ``reason`` +
    ``status`` drive the rejection response (413 too-large / 415 unsupported)."""

    ok: bool
    category: str
    limit: int
    reason: str = ""
    status: int = 200


def _env_limit(category: str) -> int | None:
    """A ``GIDEON_UPLOAD_LIMIT_<CATEGORY>`` override in bytes, if set + valid."""
    raw = os.environ.get(f"GIDEON_UPLOAD_LIMIT_{category.upper()}")
    if not raw:
        return None
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return None
    return val if val > 0 else None


def category_for(filename: str, mime: str | None = None) -> str:
    """Map a filename (+ optional browser mime) to a policy category.

    Extensions win via the archive set first, then the knowledge classifier's
    fine-grained type folds into a category. Unknown → ``other`` (still capped)."""
    from pathlib import Path

    ext = Path(filename).suffix.lower()
    if ext in _ARCHIVE_EXT:
        return "archive"
    ktype = classify(filename, mime)
    if ktype is None:
        return "other"
    return _KNOWLEDGE_TYPE_TO_CATEGORY.get(ktype, "other")


def limit_for_category(category: str) -> int:
    """The effective byte limit for a category (env override else default)."""
    if category not in _DEFAULT_LIMITS:
        category = "other"
    return _env_limit(category) or _DEFAULT_LIMITS[category]


def limit_for(filename: str, mime: str | None = None) -> int:
    """The effective byte limit for a specific file."""
    return limit_for_category(category_for(filename, mime))


def max_category_limit() -> int:
    """The largest effective per-category limit — the transport ceilings track this
    (+ multipart overhead) so the per-type policy, not the transport, is the gate."""
    return max(limit_for_category(c) for c in UPLOAD_CATEGORIES)


def single_post_threshold() -> int:
    """Byte size above which the client should use the resumable chunked protocol."""
    raw = os.environ.get("GIDEON_UPLOAD_SINGLE_POST_THRESHOLD")
    if raw:
        try:
            val = int(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            pass
    return _SINGLE_POST_THRESHOLD


def check_upload(
    filename: str,
    mime: str | None = None,
    *,
    size: int | None = None,
    override_limit: int | None = None,
) -> UploadCheck:
    """Check a file's category + size against the policy.

    Call once up front when the size is known (resumable ``init``), or repeatedly
    as bytes stream in (single-POST handlers pass the running total as ``size`` and
    reject the instant it exceeds the cap). ``override_limit`` lets a surface cap
    lower than the category default when a downstream consumer is the real
    constraint (e.g. STT can't handle the full 1 GB audio cap).
    """
    category = category_for(filename, mime)
    limit = limit_for_category(category)
    if override_limit is not None and override_limit < limit:
        limit = override_limit
    if size is not None and size > limit:
        return UploadCheck(
            ok=False,
            category=category,
            limit=limit,
            reason=f"{category} file too large (max {_human(limit)})",
            status=413,
        )
    return UploadCheck(ok=True, category=category, limit=limit)


def limits_table() -> dict[str, int]:
    """The effective per-category limits (env-resolved) — served to the client so it
    can pre-check and message before uploading."""
    return {c: limit_for_category(c) for c in UPLOAD_CATEGORIES}


def _human(n: int) -> str:
    """Compact human byte size for user-facing rejection messages."""
    if n >= _GB:
        v = n / _GB
        return f"{v:.0f} GB" if v == int(v) else f"{v:.1f} GB"
    v = n / _MB
    return f"{v:.0f} MB" if v == int(v) else f"{v:.1f} MB"
