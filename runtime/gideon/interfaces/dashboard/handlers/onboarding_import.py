"""Destination-derived import previews and fingerprint-only commits."""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from gideon.core.http_request import (
    RequestBodyTypeError,
    read_json_body,
)
from gideon.http_errors import json_error

logger = logging.getLogger(__name__)

_MAX_MESSAGE = 200


def _redacted(exc: BaseException) -> str:
    """The failure's own words, screened once and clamped to a line.

    Screened HERE, at the one boundary where an exception becomes a user-visible
    string: a writer's ``OSError`` names a path, and a path from a foreign root can
    itself look like a credential. Screening at entry (rather than composing a
    sentence first and screening that) is what keeps the redactor from eating a
    field name it was never shown.
    """
    from gideon.cognition.onboarding_import.floors import safe_text

    cleaned, _ = safe_text(str(exc) or exc.__class__.__name__)
    return cleaned[:_MAX_MESSAGE]


def _scan_with_ledger() -> tuple[list, set[str]]:
    """One thread hop for both reads: the foreign roots, then our fingerprint ledger."""
    from gideon.cognition.onboarding_import import already_imported, scan_all

    results = scan_all()
    from gideon.cognition.onboarding_import import plans
    return results, plans(results)


async def api_onboarding_import_scan(request: web.Request) -> web.Response:
    """GET /api/onboarding/import — what each source holds, and what is already ours.

    ``sources`` carries EVERY registered source, each with ``detected`` computed
    server-side (present on this machine AND holding something) — so the step can
    both list what was found and name what it looked for, from one list, without
    re-deriving "detected" on the client. ``categories`` is the closed category
    vocabulary in declaration order, so the checkbox list cannot drift from the
    writers' dispatch table.
    """
    from gideon.cognition.onboarding_import import ImportCategory, detected

    try:
        results, known = await asyncio.to_thread(_scan_with_ledger)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — a scan fault is reported, never a blank step
        logger.warning("onboarding import: scan failed", exc_info=True)
        return json_error(
            "onboarding_import_failed",
            message=f"The scan for other agent tools failed: {_redacted(exc)}",
            status=500,
        )

    found = {result.source for result in detected(results)}
    sources = []
    for result in results:
        payload = result.to_dict()
        payload["detected"] = result.source in found
        for item in payload["items"]:
            plan = known[item["fingerprint"]]
            item.update(state=plan.state.value, destination=plan.destination, detail=plan.detail,
                        existing=plan.state.value == "existing",
                        preselected=item["preselected"] and plan.state.value == "new")
        sources.append(payload)

    return web.json_response(
        {
            "sources": sources,
            "categories": [category.value for category in ImportCategory],
        }
    )


def _fingerprints(body: dict) -> list[str]:
    import re

    if set(body) != {"fingerprints"}:
        raise ValueError("Choose individual item fingerprints only")
    entries = body["fingerprints"]
    if not isinstance(entries, list) or not entries or len(entries) > 10000:
        raise ValueError("Choose between 1 and 10000 item fingerprints")
    if any(not isinstance(entry, str) or re.fullmatch(r"[0-9a-f]{16}", entry) is None for entry in entries):
        raise ValueError("Each fingerprint must be 16 lowercase hexadecimal characters")
    return list(dict.fromkeys(entries))


async def api_onboarding_import_run(request: web.Request) -> web.Response:
    from gideon.cognition.onboarding_import import run_import, scan_all

    try:
        body = await read_json_body(request)
    except RequestBodyTypeError:
        return json_error("invalid_body", status=400)
    except Exception:
        return json_error("invalid_json", status=400)
    try:
        fingerprints = _fingerprints(body)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    try:
        report = await asyncio.to_thread(lambda: run_import(scan_all(), fingerprints=fingerprints))
    except Exception as exc:
        logger.warning("onboarding import: write failed", exc_info=True)
        return json_error("onboarding_import_failed", message=f"The import stopped after a write failed: {_redacted(exc)}. Anything already imported was recorded, so importing again is safe.", status=500)
    return web.json_response(report.to_dict())


def register_onboarding_import_routes(app: web.Application) -> None:
    """Register the two /api/onboarding/import routes."""
    app.router.add_get("/api/onboarding/import", api_onboarding_import_scan)
    app.router.add_post("/api/onboarding/import", api_onboarding_import_run)
