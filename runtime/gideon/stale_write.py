"""Canonical revisions and refusals for whole-document writes."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from aiohttp import web

from gideon.http_errors import json_error

REVISION_HEADER = "If-Match"
OUTCOME_STALE_WRITE = "denied_stale_write"
OUTCOME_REVISION_REQUIRED = "denied_revision_required"


def revision_of(document: Any) -> str:
    """Return a stable digest for the exact JSON projection a reader received."""
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str
    )
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()[:16]


def claimed_revision(request: web.Request) -> str:
    """Read bare, quoted, or weak quoted If-Match values."""
    raw = (request.headers.get(REVISION_HEADER) or "").strip()
    if raw.startswith("W/"):
        raw = raw[2:].strip()
    return raw.strip('"').strip()


def stale_write_refusal(
    request: web.Request, current: Any, *, what: str
) -> web.Response | None:
    """Refuse a missing or stale base; the response never reveals the current revision."""
    claimed = claimed_revision(request)
    if not claimed:
        return json_error(
            "revision_required",
            message=(
                f"This write replaces {what}, so it must name the revision it was based on "
                f"in {REVISION_HEADER}. Read {what} again to get its revision."
            ),
            status=428,
        )
    if claimed != revision_of(current):
        return json_error(
            "stale_write",
            message=(
                f"{what} changed after the copy being saved was read. Nothing was saved; "
                "reload, reapply the change, and save again."
            ),
            status=409,
        )
    return None


def refusal_outcome(refusal: web.Response) -> str:
    """Return the audit outcome that matches the wire refusal."""
    return OUTCOME_REVISION_REQUIRED if refusal.status == 428 else OUTCOME_STALE_WRITE
