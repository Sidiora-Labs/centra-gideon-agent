"""HTTP API for zero-key local-model onboarding — ``/api/onboarding/local-models``.

Three routes, and the split between them IS the safety property:

``GET /api/onboarding/local-models``
    Detect. Probes the loopback default (``127.0.0.1:11434``) with a short timeout
    and answers what it found. Safe to call on mount and to poll, because loopback
    is the only address it can reach — the handler takes no target from the caller,
    so there is nothing to point elsewhere.

``POST /api/onboarding/local-models/scan``
    Scan, and ONLY on this call. The caller supplies the private addresses or CIDRs
    to probe and a time budget; the runtime enforces the address class, the count,
    the concurrency and a hard wall clock. **POST, not GET**, deliberately: a read
    must never be able to put packets on a user's LAN, and a method that cannot be
    reached by a prefetch, a link, or a polling ``useQuery`` is the structural half
    of "never automatically".

``POST /api/onboarding/local-models/bind``
    Bind. Re-probes the endpoint live and writes the provider row only if that probe
    succeeds, so a binding can never outlive the evidence for it — a host that has
    gone away since the scan is refused here rather than written and discovered
    broken at first chat.

**No key is requested and none is stored.** The bind body has no key field, the
written row carries no ``credential`` and no key in ``options``, and no credentials
store is touched. That is how the existing provider config already spells "this
endpoint needs no key" (the SDK's ``_anon_credential`` covers the client
constructor), so nothing new is invented to represent it.

The engine lives in :mod:`gideon.cognition.onboarding_local_models`; this module is
the wire shape, the validation, and the config write.
"""

from __future__ import annotations

import json as _json
import logging
import re
from typing import Any

from aiohttp import web

from gideon.http_errors import json_error

logger = logging.getLogger(__name__)

_MAX_TARGET_ROWS = 64

#: A provider name is a config key and a route segment, so it stays a plain slug.
_SAFE_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,62}")


async def api_local_models_detect(request: web.Request) -> web.Response:
    """GET /api/onboarding/local-models — is a local Ollama running?

    Answers ``{detected, requires_key: false, endpoint, models, detail}``. Never
    reaches a private-network address: the probe list is the module's loopback
    default and the caller cannot influence it.
    """
    from gideon.cognition import onboarding_local_models as engine

    result = await engine.detect_local_ollama(engine.DEFAULT_LOCAL_ENDPOINTS)
    return web.json_response(
        {
            **result.to_dict(),
            "detected": result.ok,
            "scan_limits": _scan_limits(),
        }
    )


def _scan_limits() -> dict[str, Any]:
    """The bounds a scan is held to, so the screen can state them before asking."""
    from gideon.cognition import onboarding_local_models as engine

    return {
        "max_targets": engine.SCAN_MAX_TARGETS,
        "max_budget_s": engine.SCAN_MAX_BUDGET_S,
        "default_budget_s": engine.SCAN_DEFAULT_BUDGET_S,
        "max_concurrency": engine.SCAN_MAX_CONCURRENCY,
        "ports": list(engine.OLLAMA_PORTS),
    }


async def _body(request: web.Request) -> dict[str, Any] | web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        return json_error("invalid_json", status=400)
    if not isinstance(body, dict):
        return json_error("invalid_body", status=400)
    return body


async def api_local_models_scan(request: web.Request) -> web.Response:
    """POST /api/onboarding/local-models/scan — one explicit, bounded LAN scan.

    Body: ``{"targets": ["192.168.1.0/28", "10.0.0.7"], "budget_s": 5}``. Every
    target is expanded and classified before a socket opens, so a public address, an
    over-wide prefix, a hostname, or too many hosts is a ``400
    local_model_scan_refused`` and nothing is probed at all.
    """
    from gideon.cognition.onboarding_local_models import (
        ScanRefused,
        clamp_budget,
        scan_private_hosts,
    )

    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    targets = body.get("targets")
    if not isinstance(targets, list) or not targets:
        return json_error(
            "local_model_scan_refused",
            message="'targets' must be a non-empty list of private addresses or CIDRs",
            status=400,
        )
    if len(targets) > _MAX_TARGET_ROWS:
        return json_error(
            "local_model_scan_refused",
            message=f"at most {_MAX_TARGET_ROWS} target entries per scan",
            status=400,
        )
    try:
        report = await scan_private_hosts(
            targets, budget_s=clamp_budget(body.get("budget_s"))
        )
    except ScanRefused as exc:
        return json_error("local_model_scan_refused", message=str(exc), status=400)
    return web.json_response(
        {
            "targets": report.targets,
            "probed": report.probed,
            "offers": [o.to_dict() for o in report.offers],
            "unreachable": report.unreachable,
            "budget_s": round(report.budget_s, 3),
            "elapsed_s": round(report.elapsed_s, 3),
            "exhausted_budget": report.exhausted_budget,
            "notes": list(report.notes),
        }
    )


async def api_local_models_bind(request: web.Request) -> web.Response:
    """POST /api/onboarding/local-models/bind — create the key-less provider row.

    Body: ``{"endpoint": "http://127.0.0.1:11434", "name"?: str, "model"?: str}``.
    The endpoint is re-probed live first; a host that does not answer as an Ollama
    right now is refused with ``400 local_model_probe_failed`` and nothing is
    written. On success the row lands in ``config.json`` with no credential and no
    key material of any kind, and the live registry picks it up.
    """
    from gideon.cognition.onboarding_local_models import (
        PROVIDER_TYPE,
        binding_entry,
        probe_ollama,
    )

    body = await _body(request)
    if isinstance(body, web.Response):
        return body
    endpoint = str(body.get("endpoint") or "").strip()
    if not endpoint:
        return json_error(
            "invalid_request", message="'endpoint' is required", status=400
        )
    name = str(body.get("name") or PROVIDER_TYPE).strip() or PROVIDER_TYPE
    if not _SAFE_NAME.fullmatch(name):
        return json_error("invalid_name", status=400)

    probe = await probe_ollama(endpoint)
    if not probe.ok:
        return json_error(
            "local_model_probe_failed",
            message=(
                f"{endpoint} did not answer as a local model service "
                f"({probe.detail or 'no response'}) — nothing was bound"
            ),
            status=400,
        )

    model = str(body.get("model") or "").strip()
    if model and model not in probe.models:
        return json_error(
            "local_model_probe_failed",
            message=f"{endpoint} does not serve a model named {model!r}",
            status=400,
        )
    if not model and probe.models:
        model = probe.models[0]

    entry = binding_entry(probe.endpoint, name=name, model=model)
    written = await _write_provider_row(entry)
    if isinstance(written, web.Response):
        return written

    _register_live(entry)
    return web.json_response(
        {
            **probe.to_dict(),
            "ok": True,
            "name": name,
            "model": model,
            "bound": True,
        }
    )


async def _write_provider_row(entry: dict[str, Any]) -> None | web.Response:
    """Append (or refresh) the provider row in ``config.json``, key material absent.

    Same lock and same atomic write the ``/api/model-providers`` create path uses, so
    a binding made here and one made there cannot interleave into a corrupt file. An
    existing row of the same name is updated rather than refused: re-running a
    one-click bind after moving the service is the ordinary case, and a 409 would ask
    the first-run screen to teach conflict resolution.
    """
    from gideon.core.atomic_write import atomic_write
    from gideon.core.config.loader import config_path
    from gideon.interfaces.dashboard.handlers.agents import _get_config_lock

    async with _get_config_lock():
        path = config_path()
        try:
            data = (
                _json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            )
        except Exception:
            data = {}
        if not isinstance(data, dict):
            data = {}
        providers = data.setdefault("providers", [])
        if not isinstance(providers, list):
            return json_error(
                "local_model_bind_failed",
                message="config.json 'providers' is not a list",
                status=500,
            )
        existing = next(
            (
                row
                for row in providers
                if isinstance(row, dict) and row.get("name") == entry["name"]
            ),
            None,
        )
        if existing is None:
            providers.append(dict(entry))
        else:
            existing.pop("credential", None)
            existing.update(
                {
                    "type": entry["type"],
                    "model": entry["model"],
                    "options": {
                        **{
                            k: v
                            for k, v in (existing.get("options") or {}).items()
                            if k not in ("api_key", "apiKey", "key")
                        },
                        **entry["options"],
                    },
                }
            )
        try:
            atomic_write(path, _json.dumps(data, indent=2) + "\n", fsync=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("local-model bind: config write failed", exc_info=True)
            return json_error(
                "local_model_bind_failed", message=str(exc)[:200], status=500
            )
    return None


def _register_live(entry: dict[str, Any]) -> None:
    """Make the new row usable without a gateway restart, best-effort.

    Best-effort for the reason the neighbouring create path's is: the row is already
    durably on disk and will be picked up at the next config sync, so a registry that
    has no such provider type loaded (its app not installed) must not turn a
    successful bind into a 500.
    """
    try:
        from gideon.integrations.llm.registry import (
            ProviderEntry,
            canonical_provider_type,
            get_default_registry,
        )

        registry = get_default_registry()
        registry_type = canonical_provider_type(entry["type"])
        try:
            capabilities = registry.capability_of(registry_type).capabilities
        except Exception:
            capabilities = frozenset()
        registry.register_entry(
            ProviderEntry(
                name=entry["name"],
                type=registry_type,
                model=entry["model"],
                options=dict(entry["options"]),
                credential=None,
                declared_capabilities=capabilities,
            )
        )
    except Exception:
        logger.debug("local-model bind: live registration skipped", exc_info=True)


def register_onboarding_local_model_routes(app: web.Application) -> None:
    """Register the three /api/onboarding/local-models routes.

    The scan is POST-only on purpose — see the module docstring.
    """
    app.router.add_get("/api/onboarding/local-models", api_local_models_detect)
    app.router.add_post("/api/onboarding/local-models/scan", api_local_models_scan)
    app.router.add_post("/api/onboarding/local-models/bind", api_local_models_bind)
