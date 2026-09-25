"""Bounded API metadata from live registrations, never handler execution."""

import re
import weakref
from collections.abc import Callable

from aiohttp import web

from gideon.extensions.apps.app_events import PLATFORM_EVENTS

CATALOG_PATH = "/api/capabilities/platform/catalog"
SAFE_READS = frozenset({CATALOG_PATH, "/api/prompts/syntax"})
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z")
_PATH = re.compile(r"/api/[A-Za-z0-9_./{}:-]{1,500}\Z")
_application = None


def bind_application(app: web.Application) -> None:
    global _application
    _application = weakref.ref(app)


def current_catalog(**options):
    app = _application() if _application is not None else None
    if app is None:
        raise RuntimeError("The dashboard route registry is not available")
    return build_catalog(app, **options)


def _symbol(value: object) -> str | None:
    return value if isinstance(value, str) and _SYMBOL.fullmatch(value) else None


def build_catalog(
    app: web.Application,
    *,
    admit: Callable[[str, str], bool] | None = None,
    offset: int = 0,
    limit: int = 100,
) -> dict:
    if not 0 <= offset <= 100_000 or not 1 <= limit <= 200:
        raise ValueError("offset must be 0..100000 and limit must be 1..200")
    routes = {}
    for route in app.router.routes():
        path = getattr(route.resource, "canonical", "")
        method = route.method
        if method not in {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}:
            continue
        if not isinstance(path, str) or not _PATH.fullmatch(path):
            continue
        if admit is not None and not admit(method, path):
            continue
        routes[(path, method)] = {
            "method": method,
            "path": path,
            "name": _symbol(route.name),
            "handler": _symbol(getattr(route.handler, "__name__", None)),
            "schema": {"status": "unknown"},
            "executable": method == "GET" and path in SAFE_READS,
        }
    ordered = [routes[key] for key in sorted(routes)]
    end = offset + limit
    events = [
        {
            "name": event.name,
            "transport": "app-inbox",
            "payload_keys": list(event.payload_keys),
            "schema": {"status": "partial"},
        }
        for event in sorted(PLATFORM_EVENTS.values(), key=lambda item: item.name)
    ]
    return {
        "version": 1,
        "routes": ordered[offset:end],
        "events": events,
        "total": len(ordered),
        "offset": offset,
        "limit": limit,
        "next_offset": end if end < len(ordered) else None,
    }
