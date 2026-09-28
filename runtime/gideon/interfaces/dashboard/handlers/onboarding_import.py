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
ACTIVITY = "onboarding_import_activity"


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


def _scan_with_ledger(activity) -> tuple[list, set[str]]:
    """Answer from bounded transcript looks while a background pass completes them."""
    from gideon.cognition.onboarding_import import scan_all
    from gideon.cognition.onboarding_import import plans
    with activity.scanning():
        results = scan_all(look=True)
        known = plans(results)
    activity.read_behind(results)
    return results, known


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
        activity = request.app[ACTIVITY]
        results, known = await asyncio.to_thread(_scan_with_ledger, activity)
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
        payload["reading"] = {"read": result.conversation_files - len(result.unread),
                              "of": result.conversation_files}
        for item in payload["items"]:
            plan = known[item["fingerprint"]]
            item.update(state=plan.state.value, destination=plan.destination, detail=plan.detail,
                        existing=plan.state.value == "existing",
                        preselected=item["preselected"] and plan.state.value == "new")
        sources.append(payload)

    activity_state = request.app[ACTIVITY].status()
    reading = activity_state["reading"]
    return web.json_response(
        {
            "sources": sources,
            "categories": [category.value for category in ImportCategory],
            "reading": reading,
        }
    )


def _fingerprints(body: dict) -> list[str]:
    import re

    if "fingerprints" not in body or set(body) - {"fingerprints", "accepted"}:
        raise ValueError("Choose individual item fingerprints only")
    entries = body["fingerprints"]
    if not isinstance(entries, list) or not entries or len(entries) > 100000:
        raise ValueError("Choose between 1 and 100000 item fingerprints")
    if any(not isinstance(entry, str) or re.fullmatch(r"[0-9a-f]{16}", entry) is None for entry in entries):
        raise ValueError("Each fingerprint must be 16 lowercase hexadecimal characters")
    return list(dict.fromkeys(entries))


def _accepted(body: dict, fingerprints: list[str]) -> dict[str, str]:
    import re

    accepted = body.get("accepted", {})
    if not isinstance(accepted, dict) or len(accepted) > 10000 or any(
        not isinstance(key, str) or key not in fingerprints or
        not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{16}", value) is None
        for key, value in accepted.items()
    ):
        raise ValueError("'accepted' must map a selected item fingerprint to its warning consent digest")
    return accepted


async def api_onboarding_import_run(request: web.Request) -> web.Response:
    try:
        body = await read_json_body(request)
    except RequestBodyTypeError:
        return json_error("invalid_body", status=400)
    except Exception:
        return json_error("invalid_json", status=400)
    try:
        fingerprints = _fingerprints(body)
        accepted = _accepted(body, fingerprints)
    except ValueError as exc:
        return json_error("invalid_request", message=str(exc), status=400)
    job, started = request.app[ACTIVITY].start_import(fingerprints, accepted)
    if not started:
        return web.json_response({"error": {"code": "import_running",
                                             "message": "An import is already running. Wait for it, or stop it first."},
                                  "job": job.to_dict(include_report=False)}, status=409)
    return web.json_response({"job": job.to_dict(include_report=False)}, status=202)


async def api_onboarding_import_job(request: web.Request) -> web.Response:
    job = request.app[ACTIVITY].job
    return web.json_response({"job": job.to_dict() if job is not None else None})


async def api_onboarding_import_stop(request: web.Request) -> web.Response:
    job = request.app[ACTIVITY].job
    if job is None or not job.running:
        return json_error("not_running", message="No import is running.", status=409)
    job.stop()
    return web.json_response({"job": job.to_dict(include_report=False)}, status=202)


async def _stop_activity(app: web.Application) -> None:
    await asyncio.to_thread(app[ACTIVITY].shutdown)


def register_onboarding_import_routes(app: web.Application) -> None:
    """Register the two /api/onboarding/import routes."""
    from gideon.cognition.onboarding_import.activity import ImportActivity

    app[ACTIVITY] = ImportActivity()
    app.on_shutdown.append(_stop_activity)
    app.router.add_get("/api/onboarding/import", api_onboarding_import_scan)
    app.router.add_post("/api/onboarding/import", api_onboarding_import_run)
    app.router.add_get("/api/onboarding/import/job", api_onboarding_import_job)
    app.router.add_delete("/api/onboarding/import/job", api_onboarding_import_stop)
