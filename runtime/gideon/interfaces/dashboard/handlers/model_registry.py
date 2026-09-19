"""Unified model discovery and active-model assignment API.

Endpoints:
    GET    /api/models/available           — discover models from all configured providers
    GET    /api/models/local/availability  — bounded local-provider availability probes
    GET    /api/models/local/health        — local readiness, residency, and sidecar health
    POST   /api/models/local/{provider}/selftest — serialized real inference probes
    GET    /api/models/active              — active models per use-case
    PUT    /api/models/active/{use_case}   — set active model(s) for a use-case
    GET    /api/models/chat                — active chat models (for dropdown use)
    GET    /api/models/huggingface/auth    — masked Hugging Face auth status
    PUT    /api/models/huggingface/auth    — validate and store a Hugging Face token
    DELETE /api/models/huggingface/auth    — remove Gideon's stored Hugging Face token

Local-model download / delete / search is served generically by the local-model routes
(``/api/models/downloads`` + ``/api/models/local/{provider}/…``), driven by the one
local-model registry — no per-kind catalog/delete/recommendation routes live here.
"""

import asyncio
import json
import logging
from typing import Any

from aiohttp import web

from gideon.extensions.providers.use_cases import (
    USE_CASES,
    VALID_USE_CASES,
    load_active_models,
    save_active_models,
)

logger = logging.getLogger(__name__)

_CATALOG_BUILD_CONCURRENCY = 4
_CATALOG_BUILD_TIMEOUT_SECS = 30.0


def _sel_log(
    op: str, outcome: str, resources: str, request: "web.Request", error: str = ""
) -> None:
    """Record a model-binding mutation in the security event log (#45 — every
    state-changing provider op is auditable, mirroring the app-lifecycle handlers).
    Best-effort: never let an audit failure break the request."""
    try:
        from gideon.security.sel import sel as _s

        _s().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome=outcome,
            source="models",
            resources=resources,
            error=error,
        )
    except Exception:
        pass


def _huggingface_owner_only(request: "web.Request") -> web.Response | None:
    app_name = request.get("app", "")
    if not app_name:
        return None
    _sel_log(
        "models.huggingface_auth",
        "denied",
        "huggingface",
        request,
        error="Hugging Face credentials are owner-only",
    )
    return web.json_response(
        {"error": "Hugging Face credentials are owner-only"}, status=403
    )


async def api_huggingface_auth_status(request: web.Request) -> web.Response:
    """GET /api/models/huggingface/auth — masked token presence and validation state."""
    denied = _huggingface_owner_only(request)
    if denied is not None:
        return denied
    from gideon.integrations.local_models.huggingface_auth import auth_status

    refresh = str(request.query.get("refresh") or "").lower() in ("1", "true", "yes")
    return web.json_response(await auth_status(force=refresh))


async def api_huggingface_auth_put(request: web.Request) -> web.Response:
    """PUT /api/models/huggingface/auth — validate, then store one token."""
    denied = _huggingface_owner_only(request)
    if denied is not None:
        return denied
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict) or not isinstance(body.get("token"), str):
        return web.json_response(
            {"error": "token must be a non-empty string"}, status=400
        )

    from gideon.integrations.local_models.huggingface_auth import (
        mask_token,
        save_token,
        validate_token,
    )

    token = body["token"].strip()
    if not token:
        return web.json_response(
            {"error": "token must be a non-empty string"}, status=400
        )
    validation = await validate_token(token, force=True)
    if validation.state != "valid":
        status = 400 if validation.state in ("invalid", "unconfigured") else 503
        _sel_log(
            "models.huggingface_auth_set",
            "error",
            "huggingface",
            request,
            error=(
                "token rejected by Hugging Face"
                if validation.state == "invalid"
                else "Hugging Face validation unavailable"
            ),
        )
        return web.json_response(
            {
                "configured": False,
                "state": validation.state,
                "valid": validation.valid,
                "error": validation.error,
            },
            status=status,
        )

    masked = mask_token(token)
    try:
        save_token(token)
    except Exception:
        _sel_log(
            "models.huggingface_auth_set",
            "error",
            "huggingface:credential_store",
            request,
            error="credential store write failed",
        )
        del token
        logger.warning("Hugging Face token write failed", exc_info=True)
        return web.json_response(
            {"error": "Could not store the Hugging Face token"}, status=500
        )
    _sel_log(
        "models.huggingface_auth_set",
        "ok",
        "huggingface:credential_store",
        request,
    )
    return web.json_response(
        {
            "configured": True,
            "source": "credential_store",
            "masked_token": masked,
            "state": "valid",
            "valid": True,
            "username": validation.username,
            "error": "",
            "cached": False,
            "checked_at": validation.checked_at,
            "expires_at": validation.expires_at,
        }
    )


async def api_huggingface_auth_delete(request: web.Request) -> web.Response:
    """DELETE /api/models/huggingface/auth — remove Gideon's stored token only."""
    denied = _huggingface_owner_only(request)
    if denied is not None:
        return denied
    from gideon.integrations.local_models.huggingface_auth import (
        auth_status,
        delete_stored_token,
    )

    if not delete_stored_token():
        _sel_log(
            "models.huggingface_auth_delete",
            "error",
            "huggingface:credential_store",
            request,
            error="no stored token",
        )
        return web.json_response({"error": "No stored Hugging Face token"}, status=404)
    _sel_log(
        "models.huggingface_auth_delete",
        "ok",
        "huggingface:credential_store",
        request,
    )
    return web.json_response({"deleted": True, **(await auth_status())})


async def api_huggingface_auth_test(request: web.Request) -> web.Response:
    """POST /api/models/huggingface/auth/test — force a fresh guarded whoami check."""
    denied = _huggingface_owner_only(request)
    if denied is not None:
        return denied
    from gideon.integrations.local_models.huggingface_auth import auth_status

    status = await auth_status(force=True)
    http_status = 200
    if status["state"] == "invalid":
        http_status = 400
    elif status["state"] == "unavailable":
        http_status = 503
    return web.json_response(status, status=http_status)


def _get_providers_from_config() -> list[dict[str, Any]]:
    from gideon.core.config.loader import config_path

    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
        return data.get("providers", [])
    except Exception:
        return []


def _catalog_for_config_provider(p: dict[str, Any]):
    """Build a ModelCatalog for a raw config.json provider dict, or None.

    Model discovery routes every provider through its registered catalog (the
    generic seam) instead of a per-type switch. The config type may be a branded
    OpenAI/Anthropic-compatible alias (together/groq/…); canonicalize it to the
    base registry type the catalog is keyed on. Returns None when no catalog is
    registered for the type (its app not loaded) — the caller treats that as "no
    models", never an error."""
    from gideon.integrations.llm.registry import (
        ProviderEntry,
        canonical_provider_type,
        get_default_registry,
    )

    ptype = canonical_provider_type(p.get("type", ""))
    entry = ProviderEntry(
        name=p.get("name", ""),
        type=ptype,
        model=p.get("model", ""),
        options=dict(p.get("options") or {}),
    )
    return get_default_registry().build_catalog(entry)


async def _discover_image_gen_models() -> list[dict[str, Any]]:
    """Discover image-generation models from the image_gen registry.

    The image_gen providers (the OpenAI-Images adapter built per OpenAI-family
    config provider + any bespoke bundle like FAL) own their own model catalogs
    that the chat/embedding discovery above doesn't see. Surface them here, tagged
    image_gen, so they appear in the Settings -> Models 'Image · Generation' row.
    Each model id is namespaced ``provider:model`` so the active-binding ref is
    exactly what the registry resolves.
    """
    try:
        from gideon.integrations.image_gen import registry as ig

        ig._ensure_registered()
        out: list[dict[str, Any]] = []
        for prov in ig.list_providers():
            try:
                if not await prov.is_available():
                    continue
                for m in await prov.list_models():
                    out.append(
                        {
                            "id": m.name,
                            "name": m.name,
                            "capabilities": ["image_gen"],
                            "description": m.description,
                            "downloaded": m.downloaded,
                            "provider": prov.name,
                            "provider_type": "image_gen",
                            "supports_edit": m.supports_edit,
                        }
                    )
            except Exception:  # noqa: BLE001 — one bad provider shouldn't drop the rest
                logger.debug(
                    "image_gen provider %r list_models failed", prov.name, exc_info=True
                )
        return out
    except Exception:
        logger.debug("image_gen discovery failed", exc_info=True)
        return []


async def _discover_video_gen_models() -> list[dict[str, Any]]:
    """Discover video-generation models from the video_gen registry."""
    try:
        from gideon.integrations.video_gen import registry as vg

        out: list[dict[str, Any]] = []
        for prov in vg.list_providers():
            try:
                if not await prov.is_available():
                    continue
                for m in await prov.list_models():
                    out.append(
                        {
                            "id": m.name,
                            "name": m.name,
                            "capabilities": ["video_gen"],
                            "description": m.description,
                            "provider": prov.name,
                            "provider_type": "video_gen",
                        }
                    )
            except Exception:  # noqa: BLE001
                logger.debug(
                    "video_gen provider %r list_models failed", prov.name, exc_info=True
                )
        return out
    except Exception:
        logger.debug("video_gen discovery failed", exc_info=True)
        return []


_BYTES_PER_MB = 1024 * 1024


def _fit_probe() -> tuple[Any, int | None, bool]:
    """``(host, budget_bytes, hide_unrunnable)`` — the host facts, gathered ONCE.

    Every fit answer on this surface comes from :mod:`gideon.integrations.local_models.fit`, so the
    chip on a row and the download panel's own arithmetic cannot disagree. Runs on a worker
    thread (see the call site): the first probe may shell out to ``nvidia-smi`` /
    ``system_profiler`` and reading config touches the disk — neither belongs on the loop.
    """
    from gideon.integrations.local_models import fit as _fit

    host = _fit.host_capacity()
    budget = _fit.usable_memory_bytes(host, reserve_gb=_fit.configured_reserve_gb())
    return host, budget, _fit.hide_unrunnable_default()


def _step_down_name(
    rows: list[dict[str, Any]], family: str, verdict: str, budget_bytes: int | None
) -> str | None:
    """The variant a row that cannot load should step DOWN to, or None.

    Only a ``red`` row has anywhere to step down to, and the target is the largest sibling
    that still fits per :func:`fit.largest_that_fits` — offering a variant that loads instead
    of one that OOMs. None when the row fits, the host is unmeasured, or no sibling fits:
    with nothing that fits there is nothing honest to offer. The sibling's OWN size decides,
    not the family quote, because the step-down target is a concrete download.
    """
    if verdict != "red":
        return None
    from gideon.integrations.local_models import fit as _fit

    siblings = [r for r in rows if _fit.family_key(str(r.get("name", ""))) == family]
    target = _fit.largest_that_fits(
        [float(r.get("size_mb") or 0) for r in siblings], budget_bytes
    )
    if target is None:
        return None
    for r in siblings:
        if float(r.get("size_mb") or 0) == target:
            return str(r.get("name", "")) or None
    return None


async def _bounded_catalog_build(build) -> Any:
    return await asyncio.wait_for(build(), timeout=_CATALOG_BUILD_TIMEOUT_SECS)


async def api_models_available(request: web.Request) -> web.Response:
    """GET /api/models/available — discover models from all configured providers.

    Returns {providers: [{name, type, models: [{id, name, capabilities, ...}]}], fit: {...}}.
    Includes both config-based providers (Ollama, OpenAI, etc.) and bundled
    providers (sentence-transformers, faster-whisper, piper, image-gen).

    LOCAL rows carry a hardware-fit verdict (``fit`` / ``fit_reason`` / ``fit_need_mb`` /
    ``quoted_size_mb`` / ``fit_step_down``) and the response carries the one memory budget
    they were judged against. Config-provider and image/video-gen rows carry NO fit fields:
    they have no local weights, and an absent field is how the UI knows to draw no chip.
    """
    providers_cfg = _get_providers_from_config()
    result: list[dict[str, Any]] = []

    from gideon.integrations.local_models.registry import get_provider as _local_get

    tasks = []
    for p in providers_cfg:
        ptype = p.get("type", "")
        pname = p.get("name", "")
        if _local_get(pname) is not None:
            continue
        catalog = _catalog_for_config_provider(p)
        if catalog is None:
            result.append({"name": pname, "type": ptype, "models": []})
            continue
        tasks.append((pname, ptype, catalog.list_models))

    if tasks:
        semaphore = asyncio.Semaphore(_CATALOG_BUILD_CONCURRENCY)

        async def _build_config_catalog(build):
            async with semaphore:
                return await _bounded_catalog_build(build)

        results = await asyncio.gather(
            *(_build_config_catalog(t[2]) for t in tasks), return_exceptions=True
        )
        for (pname, ptype, _), models_or_exc in zip(tasks, results):
            if isinstance(models_or_exc, BaseException):
                result.append(
                    {
                        "name": pname,
                        "type": ptype,
                        "models": [],
                        "error": str(models_or_exc)[:200],
                    }
                )
            else:
                models = []
                for mi in models_or_exc:
                    d = mi.to_dict()
                    d["provider"] = pname
                    d["provider_type"] = ptype
                    models.append(d)
                result.append({"name": pname, "type": ptype, "models": models})

    from gideon.integrations.local_models import fit as _fit
    from gideon.integrations.local_models.registry import catalog_for as _local_catalog
    from gideon.integrations.local_models.registry import (
        registered as _local_registered,
    )

    host, budget_bytes, hide_unrunnable = await asyncio.to_thread(_fit_probe)

    local_providers = _local_registered()
    local_semaphore = asyncio.Semaphore(_CATALOG_BUILD_CONCURRENCY)

    async def _build_local_catalog(prov):
        async with local_semaphore:
            return await _bounded_catalog_build(lambda: _local_catalog(prov))

    local_results = await asyncio.gather(
        *(_build_local_catalog(prov) for _, prov in local_providers),
        return_exceptions=True,
    )
    for (pkey, prov), models_or_exc in zip(local_providers, local_results):
        if isinstance(models_or_exc, BaseException):
            logger.debug("local model catalog failed for %s: %s", pkey, models_or_exc)
            rows = []
        else:
            rows = [lm.to_dict() for lm in models_or_exc]
        sizes_by_family: dict[str, list[float]] = {}
        for d in rows:
            sizes_by_family.setdefault(
                _fit.family_key(str(d.get("name", ""))), []
            ).append(float(d.get("size_mb") or 0))
        models = []
        for d in rows:
            family = _fit.family_key(str(d.get("name", "")))
            quoted = _fit.median_variant_size_mb(sizes_by_family.get(family, []))
            own_size_mb = float(d.get("size_mb") or 0)
            assessment = _fit.fit_verdict(
                size_mb=own_size_mb or quoted,
                context_tokens=int(d.get("context_tokens") or 0),
                budget_bytes=budget_bytes,
            )
            d["provider"] = pkey
            d["provider_type"] = pkey
            d["quoted_size_mb"] = round(quoted, 1)
            d["fit"] = assessment.verdict
            d["fit_reason"] = assessment.reason
            d["fit_need_mb"] = round(assessment.need_bytes / _BYTES_PER_MB, 1)
            d["fit_step_down"] = _step_down_name(
                rows, family, assessment.verdict, budget_bytes
            )
            models.append(d)
        result.append(
            {
                "name": pkey,
                "displayName": getattr(prov, "display_name", pkey),
                "type": pkey,
                "local": True,
                "searchable": bool(getattr(prov, "searchable", False)),
                "models": models,
            }
        )

    image_gen_models = await _discover_image_gen_models()
    if image_gen_models:
        by_provider: dict[str, list[dict[str, Any]]] = {}
        for m in image_gen_models:
            by_provider.setdefault(m["provider"], []).append(m)
        for pname, models in by_provider.items():
            result.append({"name": pname, "type": "image_gen", "models": models})

    video_gen_models = await _discover_video_gen_models()
    if video_gen_models:
        by_provider_v: dict[str, list[dict[str, Any]]] = {}
        for m in video_gen_models:
            by_provider_v.setdefault(m["provider"], []).append(m)
        for pname, models in by_provider_v.items():
            result.append({"name": pname, "type": "video_gen", "models": models})

    return web.json_response(
        {
            "providers": result,
            "fit": {
                "budget_mb": (
                    None
                    if budget_bytes is None
                    else round(budget_bytes / _BYTES_PER_MB)
                ),
                "total_ram_mb": round(host.total_ram_bytes / _BYTES_PER_MB),
                "unified_memory": bool(host.unified_memory),
                "gpu_model": host.gpu_model,
                "measured": bool(host.memory_measured),
                "hide_unrunnable": bool(hide_unrunnable),
            },
        }
    )


async def api_local_models_availability(request: web.Request) -> web.Response:
    """GET /api/models/local/availability — bounded, typed provider availability."""
    from gideon.integrations.local_models.registry import availability_snapshot

    return web.json_response(await availability_snapshot())


async def api_local_models_health(request: web.Request) -> web.Response:
    """GET /api/models/local/health — readiness, loaded models, and sidecar state."""
    from gideon.integrations.local_models.residency import local_model_health_snapshot

    return web.json_response(await local_model_health_snapshot())


async def api_local_model_selftest(request: web.Request) -> web.Response:
    """POST /api/models/local/{provider}/selftest — run real capability probes.

    An omitted body tests every declared capability. ``capability`` selects one;
    ``capabilities`` selects an ordered subset. The registry owns serialization and the
    per-capability timeout, so two clicks cannot run concurrent inference or wait forever.
    """
    from gideon.integrations.local_models.registry import run_provider_self_tests

    body: Any = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            return web.json_response(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "Request body must be valid JSON",
                    }
                },
                status=400,
            )
    if not isinstance(body, dict):
        return web.json_response(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Request body must be an object",
                }
            },
            status=400,
        )

    requested: list[str] | None = None
    if "capability" in body and "capabilities" in body:
        return web.json_response(
            {
                "error": {
                    "code": "invalid_request",
                    "message": "Send capability or capabilities, not both",
                }
            },
            status=400,
        )
    if "capability" in body:
        capability = body.get("capability")
        if not isinstance(capability, str) or not capability.strip():
            return web.json_response(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "capability must be a non-empty string",
                    }
                },
                status=400,
            )
        requested = [capability]
    elif "capabilities" in body:
        capabilities = body.get("capabilities")
        if (
            not isinstance(capabilities, list)
            or len(capabilities) > 20
            or any(not isinstance(value, str) for value in capabilities)
        ):
            return web.json_response(
                {
                    "error": {
                        "code": "invalid_request",
                        "message": "capabilities must be a list of at most 20 strings",
                    }
                },
                status=400,
            )
        requested = capabilities

    provider = request.match_info.get("provider", "") or request.match_info.get(
        "name", ""
    )
    result = await run_provider_self_tests(provider, requested)
    failure = result.get("failure") or {}
    status = {
        "unknown_provider": 404,
        "busy": 409,
    }.get(str(failure.get("code") or ""), 200)
    return web.json_response(result, status=status)


async def api_models_active(request: web.Request) -> web.Response:
    """GET /api/models/active — active models per use-case.

    Returns {use_cases: {chat: [model_ids...], embedding: [model_id], ...}}.
    """
    active = load_active_models()
    normalized: dict[str, list[str]] = {}
    for uc in USE_CASES:
        normalized[uc] = active.get(uc, [])
    return web.json_response({"use_cases": normalized})


async def api_models_active_set(request: web.Request) -> web.Response:
    """PUT /api/models/active/{use_case} — set the active model CHAIN for a use-case.

    Body: {models: ["provider_name:model_id", ...]} — an ordered fallback chain
    for EVERY use case (MODEL-USE-CASES-V2): position 0 is the default, later
    entries are fallbacks resolution walks when an earlier provider's breaker is
    open or its build fails. Order is preserved verbatim.
    """
    use_case = request.match_info["use_case"]
    if use_case not in VALID_USE_CASES:
        return web.json_response(
            {"error": f"Invalid use case: {use_case!r}; valid: {list(USE_CASES)}"},
            status=400,
        )

    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    if "models" not in body:
        _sel_log(
            "models.active_set",
            "error",
            use_case,
            request,
            error=f"body has no 'models' key (got: {sorted(body)})",
        )
        return web.json_response(
            {
                "error": {
                    "code": "models_required",
                    "message": (
                        "Body must include a 'models' key holding an ordered list of "
                        '"provider:model_id" refs. To clear this use-case\'s binding, '
                        'send {"models": []} explicitly.'
                    ),
                    "received_keys": sorted(str(k) for k in body),
                }
            },
            status=400,
        )

    models = body.get("models", [])
    if not isinstance(models, list):
        return web.json_response({"error": "models must be a list"}, status=400)

    if len(models) > 20:
        return web.json_response(
            {"error": "a fallback chain may have at most 20 entries"},
            status=400,
        )

    try:
        from gideon.extensions.providers.use_cases import (
            _known_provider_names,
            split_ref,
        )

        known = _known_provider_names()
        if known is not None:
            for m in models:
                parsed = split_ref(str(m))
                if parsed and parsed[0] not in known:
                    _sel_log(
                        "models.active_set",
                        "error",
                        f"{use_case}:{m}",
                        request,
                        error=f"unknown provider {parsed[0]!r}",
                    )
                    return web.json_response(
                        {
                            "error": f"Unknown provider {parsed[0]!r} in model ref {m!r}. "
                            f"Install/configure it first (Providers), or pick a known provider. "
                            f"Known: {sorted(known)}"
                        },
                        status=400,
                    )
    except Exception:
        logger.debug("active-model provider validation skipped", exc_info=True)

    active = load_active_models()
    active[use_case] = [str(m) for m in models]
    save_active_models(active)

    _sel_log(
        "models.active_set",
        "ok",
        f"{use_case}={','.join(active[use_case]) or '(cleared)'}",
        request,
    )
    return web.json_response(
        {"ok": True, "use_case": use_case, "models": active[use_case]}
    )


async def api_models_chat(request: web.Request) -> web.Response:
    """GET /api/models/chat — chat models for dropdowns (the one model list).

    Returns active chat models from Settings → Models when configured, else
    falls back to discovering all chat-capable models from every provider.

    Each entry carries BOTH ``model_name`` and ``model_id`` (the same bare id)
    plus ``name``/``provider``/``description`` — a superset shape so every
    consumer (composer model pill reads model_name; agent/chat pickers read
    name/model_id) works off one endpoint.
    """
    active = load_active_models()
    chat_active = active.get("chat", [])

    if chat_active:
        result = []
        for model_ref in chat_active:
            if ":" in model_ref:
                provider_name, model_id = model_ref.split(":", 1)
            else:
                provider_name, model_id = "", model_ref
            result.append(
                {
                    "name": model_id if not provider_name else model_ref,
                    "model_name": model_id,
                    "model_id": model_id,
                    "provider": provider_name,
                    "description": model_id,
                }
            )
        return web.json_response(result)

    providers_cfg = _get_providers_from_config()
    all_models: list[dict[str, Any]] = []

    def _add(pname: str, mid: str) -> None:
        all_models.append(
            {
                "name": f"{pname}/{mid}" if pname else mid,
                "model_name": mid,
                "model_id": mid,
                "provider": pname,
                "description": mid,
            }
        )

    tasks = []
    for p in providers_cfg:
        pname = p.get("name", "")
        catalog = _catalog_for_config_provider(p)
        if catalog is None:
            if p.get("model"):
                _add(pname, p["model"])
            continue
        tasks.append((pname, p.get("model", ""), catalog.list_models))

    if tasks:
        semaphore = asyncio.Semaphore(_CATALOG_BUILD_CONCURRENCY)

        async def _build_chat_catalog(build):
            async with semaphore:
                return await _bounded_catalog_build(build)

        results = await asyncio.gather(
            *(_build_chat_catalog(t[2]) for t in tasks), return_exceptions=True
        )
        for (pname, pinned, _), models_or_exc in zip(tasks, results):
            if isinstance(models_or_exc, BaseException) or not models_or_exc:
                if pinned:
                    _add(pname, pinned)
                continue
            for mi in models_or_exc:
                if "chat" in (mi.capabilities or []):
                    _add(pname, mi.id)

    return web.json_response(all_models)


def register_model_registry_routes(app: web.Application) -> None:
    """Register model registry routes.

    Local-model download/delete/search is served generically by the local-model
    routes (``/api/models/downloads`` + ``/api/models/local/{provider}/…``); no
    per-kind catalog/delete routes live here anymore."""
    app.router.add_get("/api/models/available", api_models_available)
    app.router.add_get("/api/models/local/availability", api_local_models_availability)
    app.router.add_get("/api/models/local/health", api_local_models_health)
    app.router.add_post(
        "/api/models/local/{provider}/selftest", api_local_model_selftest
    )
    app.router.add_post(
        "/api/model-providers/{name}/selftest", api_local_model_selftest
    )
    app.router.add_get("/api/models/active", api_models_active)
    app.router.add_put("/api/models/active/{use_case}", api_models_active_set)
    app.router.add_get("/api/models/chat", api_models_chat)
    app.router.add_get("/api/models/huggingface/auth", api_huggingface_auth_status)
    app.router.add_put("/api/models/huggingface/auth", api_huggingface_auth_put)
    app.router.add_delete("/api/models/huggingface/auth", api_huggingface_auth_delete)
    app.router.add_post("/api/models/huggingface/auth/test", api_huggingface_auth_test)
