"""Model-provider and agent-provider management API handlers.

Routes:
    GET    /api/model-providers              — list configured model-provider entries
    POST   /api/model-providers              — create a model provider
    PUT    /api/model-providers/{name}       — update a model provider
    DELETE /api/model-providers/{name}       — delete a model provider
    POST   /api/model-providers/{name}/test  — test a provider's connectivity
    GET    /api/model-providers/{name}/models, /search; POST .../pull, .../models/delete
    GET    /api/agent-providers              — list agent runtimes (native + acp:<cli>)
    GET    /api/agent-providers/{id}/agents  — discovered agents for a runtime
    GET    /api/agent-runners                — runner catalog rows + measured health
"""

import asyncio
import contextlib
import logging
import time
from typing import Any

from aiohttp import web

from gideon.core.http_request import read_json_body, string_field
from gideon.extensions.providers.failure_copy import relayed_failure_copy

logger = logging.getLogger(__name__)

_READINESS_TTL_SECS = 300.0
_readiness_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def _tenant_cache_key(tenant: Any) -> str:
    if hasattr(tenant, "kind"):
        return str(getattr(tenant, "tenant", "") or "self-hosted")
    return str(tenant or "self-hosted")


def _runner_consent_refusal(runtime_id: str, tenant: Any = None) -> str:
    """Return a refusal before readiness or discovery can start a custom CLI."""
    from gideon.engine.agents import runners

    definition = runners.definition_for_runtime(runtime_id)
    if definition is None or definition.source != "user":
        return ""
    if runners.owner_grant_allowed(definition, tenant=tenant):
        return ""
    return "owner approval required for this custom runner definition"


def _runner_owner_principal(request: web.Request):
    from gideon.security.approval_answer import OWNER, of_request

    principal = of_request(request)
    if principal.kind != OWNER or not principal.name:
        return None
    tenant = principal.tenant or request.get("tenant_id") or ""
    if tenant and tenant != principal.tenant:
        from gideon.security.approval_answer import Principal

        return Principal(principal.kind, principal.name, str(tenant))
    return principal


def _runner_tenant_scope(request: web.Request):
    principal = _runner_owner_principal(request)
    if principal is not None:
        return principal
    return request.get("tenant_id")


@web.middleware
async def runner_grant_tenant_middleware(request: web.Request, handler):
    """Carry authenticated tenant scope through real session and ACP startup work."""
    from gideon.security.runner_grants import tenant_scope

    with tenant_scope(_runner_tenant_scope(request)):
        return await handler(request)


def _provider_document(path) -> dict[str, Any]:
    import json

    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("providers", []), list):
        raise ValueError(
            "provider configuration must be an object with a providers list"
        )
    return data


async def api_providers_list(request: web.Request) -> web.Response:
    """GET /api/model-providers — list configured model-provider entries.

    Returns ``{providers: [{name, type, model, capabilities, credential_status}]}``.
    ``credential_status`` is ``"ok"``, ``"missing"``, or ``"unconfigured"``
    (when no credential is declared).  No secret values are included.
    """
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    entries = registry.list_entries()

    from gideon.extensions.providers.connection import (
        Connection,
        entry_fingerprint,
        get_connection_board,
    )

    board = get_connection_board()
    result: list[dict[str, Any]] = []
    for entry in entries:
        if entry.type == "acp_agent":
            continue
        if not entry.credential:
            cred_status = "ok"
        else:
            try:
                from gideon.core.config.loader import config_dir
                from gideon.integrations.llm.credentials import CredentialStore

                store = CredentialStore(config_dir() / "credentials.json")
                cred = store.resolve(entry.credential)
                cred_status = "ok" if cred.secret else "missing"
            except Exception:
                cred_status = "missing"

        try:
            cap = registry.capability_of(entry.type)
            capabilities = sorted(c.value for c in cap.capabilities)
        except Exception:
            capabilities = sorted(c.value for c in entry.declared_capabilities)

        key = (entry.name, entry_fingerprint(entry))
        cached = board._answers.get(key)
        connection = (
            cached[0]
            if cached is not None and time.monotonic() - cached[1] < board._ttl
            else Connection("untested", "Use Test to check this provider.")
        )
        result.append(
            {
                "name": entry.name,
                "type": entry.type,
                "model": entry.model,
                "capabilities": capabilities,
                "credential_status": cred_status,
                "connection": connection.to_wire(),
            }
        )

    return web.json_response({"providers": result})


async def api_provider_types(request: web.Request) -> web.Response:
    """GET /api/model-provider-types — installable model-provider types.

    Drives the "Add instance" dropdown. The list is EXACTLY the model-provider
    apps currently installed (each contributes a provider ``type`` via its
    manifest) — no hardcoded type list in the frontend. A type not backed by an
    installed app never appears, so a user can only add an instance of a provider
    whose app they've installed. Each entry carries the app's display label, the
    declared capabilities, whether it's multi-instance, and its ``settingsSchema``
    (JSON Schema + x-meta) so the form renders the right fields (api_key / region /
    endpoint enum / …) without the frontend knowing the provider.
    """
    from gideon.extensions.providers.registry import get_provider_registry

    reg = get_provider_registry()
    seen: set[str] = set()
    types: list[dict[str, Any]] = []
    for ext in reg.list_by_type("model"):
        cfg = ext.provider_config
        ptype = cfg.providerType or ext.manifest.name.replace("-models", "")
        if not ptype or ptype in seen or ptype == "acp_agent":
            continue
        seen.add(ptype)
        manifest = ext.manifest
        types.append(
            {
                "type": ptype,
                "label": manifest.displayName or manifest.name,
                "app": manifest.name,
                "capabilities": list(cfg.capabilities),
                "multiInstance": bool(cfg.multiInstance),
                "settingsSchema": cfg.settingsSchema or {},
            }
        )
    types.sort(key=lambda t: t["label"].lower())
    return web.json_response({"types": types})


async def api_agent_providers_list(request: web.Request) -> web.Response:
    """GET /api/agent-providers — the single list of agent runtimes + readiness.

    This is the one source of truth for the "Agent Providers" UI section: the
    AgentProvider *runtime* axis, spanning the in-process ``native`` runtime
    and every ``acp:<cli>`` runtime registered by a removable bundle
    (claude-code / codex / future). Cached readiness is shown when available;
    otherwise the row stays untested until an explicit Test request launches
    an ACP check.

    Returns ``{agent_providers: [{name, provider_id, type, extension, ready,
    state, detail, login_command}]}`` where ``extension`` (when present) is the
    bundle name the row's enable/config card is keyed by, so the frontend can
    merge readiness onto the extension card instead of rendering two sections.
    """
    from gideon.engine.agents.registry import get_agent_provider_class
    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    entries = registry.list_entries()

    result: list[dict[str, Any]] = []

    result.append(
        {
            "name": "native",
            "provider_id": "native",
            "type": "native",
            "extension": "native-agents",
            "ready": True,
            "state": "ready",
            "detail": "In-process agent runtime (no external CLI).",
            "login_command": None,
        }
    )

    runtime_entries = [
        entry
        for entry in entries
        if get_agent_provider_class("acp" if entry.type == "acp_agent" else entry.type)
        is not None
    ]

    from gideon.integrations.acp.connection_pool import get_acp_pool

    _pool = get_acp_pool()

    def _row(entry: Any) -> dict[str, Any]:
        options = dict(entry.options or {})
        tenant_scope = _runner_tenant_scope(request)
        refusal = _runner_consent_refusal(entry.name, tenant_scope)
        if refusal:
            status_d = {
                "ready": False,
                "state": "needs_owner_approval",
                "detail": refusal,
                "login_command": None,
            }
        elif _pool is not None and _pool.is_warmed(entry.name):
            status_d = {
                "ready": True,
                "state": "ready",
                "detail": "warmed (pooled live connection)",
                "login_command": None,
            }
        else:
            hit = _readiness_cache.get((_tenant_cache_key(tenant_scope), entry.name))
            if hit and (time.monotonic() - hit[0]) < _READINESS_TTL_SECS:
                status_d = hit[1]
            else:
                status_d = {
                    "ready": False,
                    "state": "untested",
                    "detail": "Not tested. Use Test to check this provider and model.",
                    "login_command": None,
                }

        return {
            "name": entry.name,
            "provider_id": entry.name,
            "type": entry.type,
            "extension": options.get("extension"),
            **status_d,
        }

    result.extend(_row(entry) for entry in runtime_entries)

    return web.json_response({"agent_providers": result})


async def warm_readiness_cache() -> int:
    """Keep startup read-only; ACP readiness is measured only after an operator Test."""
    from gideon.integrations.acp.connection_pool import get_acp_pool
    from gideon.integrations.llm.registry import get_default_registry

    pool = get_acp_pool()
    entries = [
        e for e in get_default_registry().list_entries() if e.type == "acp_agent"
    ]
    return sum(
        1 for entry in entries if pool is not None and pool.is_warmed(entry.name)
    )


_DISCOVERY_TTL_SECS = 600.0
_discovery_cache: dict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = {}


async def api_agent_provider_agents(request: web.Request) -> web.Response:
    """GET /api/agent-providers/{id}/agents — read previously tested agents.

    This read is cache-only. The explicit POST provider Test action is the sole
    settings discovery boundary; a stale or missing cache remains untested.

    Returns ``{agents: [{id, name, runtime, description, provider_agent,
    reasoning_effort, models}], permission_modes: [...], cached: bool}`` where
    ``permission_modes`` are the runtime's NATIVE permission modes (raw capability
    for the trust-ladder grey-out). ``native`` and unknown ids return ``[]``.
    """
    runtime_id = request.match_info.get("id", "")
    if runtime_id == "native":
        return web.json_response(
            {"agents": [], "permission_modes": [], "cached": False}
        )

    tenant_scope = _runner_tenant_scope(request)
    refusal = _runner_consent_refusal(runtime_id, tenant_scope)
    if refusal:
        return web.json_response({"error": refusal}, status=409)

    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        entry = registry.get_entry(runtime_id)
    except Exception:
        return web.json_response(
            {"error": f"unknown runtime {runtime_id!r}"}, status=404
        )
    if entry.type != "acp_agent":
        return web.json_response(
            {"error": f"{runtime_id!r} is not an ACP runtime"}, status=400
        )

    payload = _cached_discovery(runtime_id, tenant=tenant_scope)
    if payload is not None:
        return web.json_response({**payload, "cached": True})
    return web.json_response(
        {
            "agents": [],
            "permission_modes": [],
            "cached": False,
            "state": "untested",
            "detail": "Not tested. Use Test to discover agents.",
        }
    )


def _cached_discovery(runtime_id: str, *, tenant: Any = None) -> dict[str, Any] | None:
    """Return the cached discovery payload for *runtime_id* if still fresh."""
    import time as _time

    hit = _discovery_cache.get((_tenant_cache_key(tenant), runtime_id))
    if hit and (_time.monotonic() - hit[0]) < _DISCOVERY_TTL_SECS:
        return dict(hit[1][0]) if hit[1] else {}
    return None


def declared_efforts(runtime_id: str, *, tenant: Any = None) -> list[str] | None:
    """The reasoning-effort values *runtime_id* DECLARED, or ``None`` when unknown.

    Cache-only by design: discovery opens a live ACP session (~15-20 s), so a write path
    validating against this must never be the thing that triggers it. Reads the same
    payload :func:`api_agent_provider_agents` already served to the composer, so the set
    checked on the write path is exactly the set the pill was drawn from.

    **An empty list and ``None`` are different facts and must not be collapsed.**
    ``[]`` is a *declaration* — the backend was asked and reported no effort axis (codex:
    ``supported_efforts: []``), so pinning an effort is refusable. ``None`` is an
    *absence of information* — discovery has not run, is stale, or failed — and a caller
    must fall back to a format check rather than refuse a bind it cannot judge.
    ``supported_efforts`` is computed once per runtime and attached identically to every
    agent (``AcpAgentProvider.discover_agents``), so this is a runtime-level question and
    needs no per-agent disambiguation.
    """
    payload = _cached_discovery(runtime_id, tenant=tenant)
    if payload is None:
        return None
    agents = payload.get("agents") or []
    if not agents:
        return None
    first = agents[0] if isinstance(agents[0], dict) else {}
    if "supported_efforts" not in first:
        return None
    out: list[str] = []
    for row in first.get("supported_efforts") or []:
        value = str(row.get("value") or "") if isinstance(row, dict) else str(row or "")
        if value:
            out.append(value)
    return out


def _record_runtime_test(runtime_id: str, tenant: Any, payload: dict[str, Any]) -> None:
    cached = {
        "agents": list(payload.get("agents") or []),
        "permission_modes": list(payload.get("permission_modes") or []),
    }
    _discovery_cache[(_tenant_cache_key(tenant), runtime_id)] = (
        time.monotonic(),
        [cached],
    )


async def api_provider_models(request: web.Request) -> web.Response:
    """GET /api/model-providers/{name}/models — list available models for a provider entry.

    Generic across provider types: resolves the entry's registered ModelCatalog and
    returns ``catalog.list_models()``. A provider with no catalog registered (its app
    not loaded, or a provider that exposes no discovery) returns an empty list — NOT
    an error — so a keyless/non-HTTP provider (e.g. Bedrock) can never 500 here (the
    old code assumed every non-ollama provider served an OpenAI ``/v1/models`` and
    fell back to ``localhost:11434``)."""
    from gideon.integrations.llm.registry import (
        ProviderResolutionError,
        get_default_registry,
    )

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response(
            {"error": f"No provider entry named '{name}'"}, status=404
        )

    catalog = registry.build_catalog(entry)
    if catalog is None:
        return web.json_response({"models": []})
    try:
        models = await catalog.list_models()
    except Exception as exc:  # noqa: BLE001 — discovery failure is not a server error
        logger.warning("model discovery failed for provider %r", name, exc_info=True)
        return web.json_response({"models": [], "error": relayed_failure_copy(exc)})
    out = []
    for m in models:
        d = m.to_dict()
        d.setdefault("name", d.get("id", ""))
        out.append(d)
    result = {"models": out}
    from gideon.workspace.capabilities.platform.connections import ScopedCatalog

    if isinstance(catalog, ScopedCatalog):
        result["model_catalog"] = [
            model.to_dict() for model in await catalog.full_catalog()
        ]
    return web.json_response(result)


async def api_provider_model_search(request: web.Request) -> web.Response:
    """GET /api/model-providers/{name}/search?q=<query> — search a provider's
    installable model catalog.

    Generic across provider types via the ModelManager axis: a provider whose
    catalog implements ``search_catalog`` (ollama) returns results; any other
    provider (a hosted API with no installable catalog) returns an empty list.
    """
    from gideon.integrations.llm.catalog import ModelManager
    from gideon.integrations.llm.registry import (
        ProviderResolutionError,
        get_default_registry,
    )

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response(
            {"error": f"No provider entry named '{name}'"}, status=404
        )

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response({"results": []})

    q = request.rel_url.query.get("q", "").strip()
    if not q:
        return web.json_response({"error": "q parameter required"}, status=400)

    try:
        models = await catalog.search_catalog(q)
    except Exception as exc:  # noqa: BLE001
        logger.warning("catalog search failed for provider %r", name, exc_info=True)
        return web.json_response({"results": [], "error": relayed_failure_copy(exc)})
    results = [
        {"name": m.name or m.id, "description": m.description, "pulls": 0, "tags": []}
        for m in models
    ]
    return web.json_response({"results": results})


async def api_provider_model_show(request: web.Request) -> web.Response:
    """GET /api/model-providers/{name}/show?model=<m> — rich model metadata.

    Generic across provider types via the ModelManager axis. A provider whose
    catalog implements ``show_model`` (ollama) returns ``{model, family,
    parameter_size, quantization, format, context_length, capabilities,
    license_short}`` (empty fields omitted); any other provider returns 400
    "not supported". Lets a user inspect a model before binding it in
    Settings → Models."""
    from gideon.integrations.llm.catalog import ModelManager
    from gideon.integrations.llm.registry import (
        ProviderResolutionError,
        get_default_registry,
    )

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response(
            {"error": f"No provider entry named '{name}'"}, status=404
        )

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response(
            {"error": "Model detail not supported by this provider"}, status=400
        )

    model = request.rel_url.query.get("model", "").strip()
    if not model:
        return web.json_response({"error": "model parameter required"}, status=400)

    try:
        info = await catalog.show_model(model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("model detail failed for provider %r", name, exc_info=True)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)

    out = info.to_dict()
    out["model"] = model
    out.pop("id", None)
    out.pop("name", None)
    return web.json_response({k: v for k, v in out.items() if v})


async def api_provider_model_pull(request: web.Request) -> web.StreamResponse:
    """POST /api/model-providers/{name}/pull — pull (download) a model.

    Body: {model: "<model_name>"}. Generic across provider types via the
    ModelManager axis: providers whose catalog implements ``pull_model`` (ollama)
    stream progress; others return 400.

    Streams newline-delimited JSON progress frames. Each: {status, completed?,
    total?, digest?}; a terminal failure frame is {error: "..."}.
    """
    from gideon.integrations.llm.catalog import ModelManager
    from gideon.integrations.llm.registry import (
        ProviderResolutionError,
        get_default_registry,
    )

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response(
            {"error": f"No provider entry named '{name}'"}, status=404
        )

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response(
            {"error": "Model download not supported by this provider"}, status=400
        )

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    model = str(body.get("model", "")).strip()
    if not model:
        return web.json_response({"error": "model is required"}, status=400)

    import json as _json

    from aiohttp.client_exceptions import ClientConnectionResetError

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "application/x-ndjson",
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
    await resp.prepare(request)
    cancelled = False
    pull = catalog.pull_model(model)
    try:
        async for frame in pull:
            if request.transport is None or request.transport.is_closing():
                cancelled = True
                break
            try:
                await resp.write((_json.dumps(frame.to_dict()) + "\n").encode())
            except (ConnectionResetError, ClientConnectionResetError):
                cancelled = True
                break
    except (asyncio.CancelledError, ConnectionResetError, ClientConnectionResetError):
        cancelled = True
    except Exception as exc:
        logger.warning("model pull stream failed", exc_info=True)
        try:
            await resp.write(
                (_json.dumps({"error": relayed_failure_copy(exc)}) + "\n").encode()
            )
        except Exception:
            logger.debug("pull: failed to write error frame", exc_info=True)
    finally:
        with contextlib.suppress(Exception):
            aclose = getattr(pull, "aclose", None)
            if aclose is not None:
                await aclose()
    if cancelled:
        logger.info("Model pull of %r cancelled by client disconnect", model)
    with contextlib.suppress(Exception):
        await resp.write_eof()
    return resp


async def api_provider_model_delete(request: web.Request) -> web.Response:
    """POST /api/model-providers/{name}/models/delete — delete a local model.

    Body: {model: "<model_name:tag>"}. Generic across provider types via the
    ModelManager axis: providers whose catalog implements ``delete_model`` (ollama)
    delete it; others return 400.
    """
    from gideon.integrations.llm.catalog import ModelManager
    from gideon.integrations.llm.registry import (
        ProviderResolutionError,
        get_default_registry,
    )

    name = request.match_info.get("name", "")
    registry = get_default_registry()
    try:
        entry = registry.get_entry(name)
    except ProviderResolutionError:
        return web.json_response(
            {"error": f"No provider entry named '{name}'"}, status=404
        )

    catalog = registry.build_catalog(entry)
    if not isinstance(catalog, ModelManager):
        return web.json_response(
            {"error": "Model deletion not supported by this provider"}, status=400
        )

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    model = str(body.get("model", "")).strip()
    if not model:
        return web.json_response({"error": "model is required"}, status=400)

    try:
        await catalog.delete_model(model)
    except Exception as exc:  # noqa: BLE001
        logger.warning("model delete failed for provider %r", name, exc_info=True)
        return web.json_response({"error": relayed_failure_copy(exc)}, status=500)
    return web.json_response({"ok": True, "model": model})


async def api_provider_create(request: web.Request) -> web.Response:
    """POST /api/model-providers — add a new model provider to config."""

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)

    name = string_field(body, "name")
    ptype = string_field(body, "type")
    model = body.get("model", "")
    options = body.get("options", {})

    if not name or not ptype:
        return web.json_response({"error": "name and type are required"}, status=400)
    if not isinstance(model, str) or not isinstance(options, dict):
        return web.json_response(
            {"error": "model must be a string and options must be an object"},
            status=400,
        )

    from gideon.integrations.llm.registry import canonical_provider_type
    from gideon.integrations.llm.registry import get_default_registry as _gdr

    _reg = _gdr()
    _canon = canonical_provider_type(ptype)
    _known = (
        _canon in _reg._capabilities or _reg.catalog_of(_canon) is not None
    )  # noqa: SLF001
    if not _known:
        _addable = sorted(
            set(_reg._capabilities) | set(_reg._catalog_factories)
        )  # noqa: SLF001
        return web.json_response(
            {
                "error": f"Unknown provider type {ptype!r}. Install its app first. "
                f"Currently registered: {_addable}"
            },
            status=400,
        )

    from gideon.core.config.transactions import ConfigPreserveError, mutate_config_async

    entry: dict = {"name": name, "type": ptype, "model": model}
    if options:
        entry["options"] = options

    def add_provider(data: dict) -> dict:
        providers = data.get("providers")
        if providers is None:
            providers = []
            data["providers"] = providers
        if not isinstance(providers, list):
            raise ValueError(
                "provider configuration must be an object with a providers list"
            )
        if any(
            isinstance(provider, dict) and provider.get("name") == name
            for provider in providers
        ):
            return {"status": "exists"}
        providers.append(entry)
        return {"status": "created"}

    try:
        result = await mutate_config_async(add_provider)
    except ConfigPreserveError as exc:
        return web.json_response(
            {"error": f"Could not read provider configuration: {exc}"}, status=409
        )
    except ValueError as exc:
        return web.json_response(
            {"error": f"Could not read provider configuration: {exc}"}, status=409
        )
    except Exception:
        return web.json_response(
            {"error": "Could not write provider configuration"}, status=409
        )
    if result["status"] == "exists":
        return web.json_response(
            {"error": f"Provider '{name}' already exists"}, status=409
        )

    from gideon.integrations.llm.registry import (
        ProviderEntry,
        canonical_provider_type,
        get_default_registry,
    )

    registry = get_default_registry()
    try:
        registry_type = canonical_provider_type(ptype)
        cap = registry.capability_of(registry_type)
        entry_options = dict(options or {})
        if ptype != registry_type:
            entry_options["_original_type"] = ptype
        new_entry = ProviderEntry(
            name=name,
            type=registry_type,
            model=model,
            options=entry_options,
            credential=None,
            declared_capabilities=cap.capabilities,
        )
        registry.register_entry(new_entry)
    except Exception:
        pass

    _refresh_media_registries()
    return web.json_response({"ok": True, "name": name})


def _refresh_media_registries() -> None:
    """Drop the typed STT/TTS/image-gen registries so a config change re-reads.

    Remote STT/TTS/image adapters are built from config.json providers at first
    resolution; clearing the registries makes a newly added/removed/edited
    OpenAI-family endpoint selectable as the active voice/image model without a
    gateway restart.
    """
    from gideon.integrations.embedding_providers.registry import (
        refresh_providers as _embed_refresh,
    )
    from gideon.integrations.image_gen.registry import refresh_providers as _img_refresh
    from gideon.integrations.stt.registry import refresh_providers as _stt_refresh
    from gideon.integrations.tts.registry import refresh_providers as _tts_refresh
    from gideon.integrations.video_gen.registry import refresh_providers as _vid_refresh

    _embed_refresh()
    _stt_refresh()
    _tts_refresh()
    _img_refresh()
    _vid_refresh()
    try:
        from gideon.integrations.local_models.registry import (
            register_config_model_managers,
        )

        register_config_model_managers()
    except Exception:
        pass


async def api_provider_update(request: web.Request) -> web.Response:
    """PUT /api/model-providers/{name} — update a provider's model, endpoint, or options."""
    import dataclasses as _dataclasses

    name = request.match_info["name"]
    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "JSON body must be an object"}, status=400)
    if "model" in body and not isinstance(body["model"], str):
        return web.json_response({"error": "model must be a string"}, status=400)
    if "options" in body and not isinstance(body["options"], dict):
        return web.json_response({"error": "options must be an object"}, status=400)

    from gideon.core.config.transactions import ConfigPreserveError, mutate_config_async

    def update_provider(data: dict) -> dict:
        providers = data.get("providers", [])
        if not isinstance(providers, list):
            raise ValueError(
                "provider configuration must be an object with a providers list"
            )
        target = next(
            (
                provider
                for provider in providers
                if isinstance(provider, dict) and provider.get("name") == name
            ),
            None,
        )
        if target is None:
            return {"status": "missing"}
        if "model" in body:
            target["model"] = body["model"]
        if "options" in body:
            options = target.setdefault("options", {})
            if not isinstance(options, dict):
                raise ValueError("provider options must be an object")
            options.update(body["options"])
        if "type" in body:
            target["type"] = body["type"]
        return {"status": "updated", "provider": dict(target)}

    try:
        result = await mutate_config_async(update_provider)
    except (ConfigPreserveError, ValueError) as exc:
        return web.json_response(
            {"error": f"Could not read provider configuration: {exc}"}, status=409
        )
    except Exception:
        return web.json_response(
            {"error": "Could not write provider configuration"}, status=409
        )
    if result["status"] == "missing":
        return web.json_response({"error": "not found"}, status=404)
    target = result["provider"]

    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        existing = registry.get_entry(name)
        updated = _dataclasses.replace(
            existing,
            model=target.get("model", existing.model),
            options=target.get("options", existing.options),
        )
        registry.unregister_entry(name)
        registry.register_entry(updated)
    except Exception:
        pass

    _refresh_media_registries()
    return web.json_response({"ok": True, "name": name})


async def api_provider_delete(request: web.Request) -> web.Response:
    """DELETE /api/model-providers/{name} — remove a provider from config."""
    name = request.match_info["name"]

    from gideon.core.config.transactions import ConfigPreserveError, mutate_config_async

    def delete_provider(data: dict) -> dict[str, bool]:
        providers = data.get("providers", [])
        if not isinstance(providers, list):
            raise ValueError(
                "provider configuration must be an object with a providers list"
            )
        if any(not isinstance(provider, dict) for provider in providers):
            raise ValueError("provider configuration contains an invalid entry")
        retained = [provider for provider in providers if provider.get("name") != name]
        if len(retained) == len(providers):
            return {"deleted": False}
        data["providers"] = retained
        return {"deleted": True}

    try:
        result = await mutate_config_async(delete_provider)
    except (ConfigPreserveError, ValueError) as exc:
        return web.json_response(
            {"error": f"Could not read provider configuration: {exc}"}, status=409
        )
    except Exception:
        return web.json_response(
            {"error": "Could not write provider configuration"}, status=409
        )
    if not result["deleted"]:
        return web.json_response({"error": "not found"}, status=404)

    from gideon.integrations.llm.registry import get_default_registry

    registry = get_default_registry()
    try:
        registry.unregister_entry(name)
    except Exception:
        pass

    _drop_provider_active_models(name)
    _refresh_media_registries()

    return web.json_response({"ok": True})


def _drop_provider_active_models(provider_name: str) -> None:
    """Remove every active-model ref (``"<provider>:<model>"``) for a provider."""
    from gideon.extensions.providers.use_cases import (
        load_active_models,
        save_active_models,
    )

    active = load_active_models()
    changed = False
    for use_case, refs in list(active.items()):
        if not isinstance(refs, list):
            continue
        kept = [r for r in refs if str(r).split(":", 1)[0] != provider_name]
        if len(kept) != len(refs):
            active[use_case] = kept
            changed = True
    if changed:
        save_active_models(active)


async def api_provider_test(request: web.Request) -> web.Response:
    """POST /api/model-providers/{name}/test — test provider connectivity.

    Generic across provider types: builds the entry's ModelCatalog and calls
    ``test_connection()``. Works for a provider still only in config.json (a
    just-created entry not yet in the live registry) by synthesizing a transient
    ProviderEntry from its stored type/options and building the catalog from that.
    A provider type with no catalog registered (its app not loaded) reports a
    benign "no discovery" status rather than erroring."""
    import json as _json

    from gideon.core.config.loader import config_path
    from gideon.integrations.llm.registry import ProviderEntry, get_default_registry

    name = request.match_info["name"]

    registry = get_default_registry()
    entry = next((e for e in registry.list_entries() if e.name == name), None)
    if entry is None:
        try:
            data = (
                _json.loads(config_path().read_text(encoding="utf-8"))
                if config_path().exists()
                else {}
            )
            p = next(
                (p for p in data.get("providers", []) if p.get("name") == name), None
            )
            if not p:
                return web.json_response({"error": "not found"}, status=404)
            options = p.get("options") or {}
            ptype = options.get("_original_type") or p.get("type", "")
            entry = ProviderEntry(
                name=name, type=ptype, model=p.get("model", ""), options=options
            )
        except Exception:
            return web.json_response({"error": "not found"}, status=404)

    if entry.type == "acp_agent":
        principal = _runner_owner_principal(request)
        if principal is None:
            return web.json_response(
                {"error": "Only the authenticated owner may test an ACP runtime."},
                status=403,
            )
        tenant = _runner_tenant_scope(request)
        refusal = _runner_consent_refusal(name, tenant)
        if refusal:
            return web.json_response({"error": refusal}, status=409)

        from gideon.engine.agents.runtime_tests import test_runtime
        from gideon.extensions.providers.connection import (
            CONNECTED,
            FAILED,
            Connection,
            entry_fingerprint,
            get_connection_board,
        )
        from gideon.extensions.providers.failure_copy import relayed_failure_copy

        tested = await test_runtime(name, entry, tenant=tenant)
        status_d = {
            "ready": tested["ready"],
            "state": tested["state"],
            "detail": tested["detail"],
            "login_command": tested["login_command"],
        }
        tenant_key = _tenant_cache_key(tenant)
        _readiness_cache[(tenant_key, name)] = (time.monotonic(), status_d)
        _record_runtime_test(name, tenant, tested)
        get_connection_board().record(
            name,
            entry_fingerprint(entry),
            Connection(
                CONNECTED if status_d["ready"] else FAILED,
                status_d["detail"],
                checked_at=time.time(),
            ),
        )
        return web.json_response(
            {
                "ok": bool(status_d["ready"]),
                "status": status_d["state"],
                "message": status_d["detail"],
                "model": entry.model,
                "agents": tested["agents"],
                "permission_modes": tested["permission_modes"],
            }
        )

    from gideon.extensions.providers.connection import (
        entry_fingerprint,
        get_connection_board,
        measure,
    )

    catalog = registry.build_catalog(entry)
    answer = await measure(catalog)
    board = get_connection_board()
    board.record(name, entry_fingerprint(entry), answer)
    if answer.state == "untestable":
        return web.json_response(
            {
                "ok": True,
                "status": "no_probe",
                "message": answer.detail,
            }
        )
    if answer.state == "connected":
        return web.json_response(
            {"ok": True, "status": "connected", "message": answer.detail}
        )
    return web.json_response({"ok": False, "status": "error", "message": answer.detail})


async def api_agent_runners_list(request: web.Request) -> web.Response:
    """GET /api/agent-runners — the BYO runner catalog with measured health evidence.

    One row per cataloged runner (EXECUTION-ISOLATION §3.1): the definition, the last
    MEASURED health evidence (``ok``/``version``/``latency_ms``/``error``/
    ``checked_at``), the capability matrix persisted from a real ACP handshake, and the
    adapter-provenance verdict the unattended-spawn gate reads.

    A plain GET is a pure read of persisted evidence, so the Settings surface paints
    instantly and never fabricates a value for a runner it has not probed —
    ``health: null`` means "never probed", not "fine". ``?probe=1`` re-measures every
    row first (one ``--version`` spawn per runner, nothing else), which is what the
    surface's refresh action calls.
    """
    from gideon.engine.agents import runners as runner_catalog

    probe = request.query.get("probe") in ("1", "true", "yes")
    tenant = _runner_tenant_scope(request)
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(
        None, lambda: runner_catalog.runner_rows(probe=probe, tenant=tenant)
    )
    from gideon.security.runner_grants import allowed, revision

    projected = []
    for row in rows:
        item = row.to_dict()
        definition = row.definition
        if definition.source == "user":
            try:
                item["owner_grant"] = {
                    "required": True,
                    "allowed": allowed(definition, tenant),
                    "revision": revision(definition, tenant),
                }
            except Exception:
                item["owner_grant"] = {
                    "required": True,
                    "allowed": False,
                    "revision": "",
                }
        else:
            item["owner_grant"] = {"required": False, "allowed": True}
        projected.append(item)
    return web.json_response({"runners": projected})


async def api_agent_runner_grant(request: web.Request) -> web.Response:
    """POST /api/agent-runners/{id}/grant for an authenticated owner decision."""
    principal = _runner_owner_principal(request)
    if principal is None:
        return web.json_response(
            {"error": "Only the authenticated owner may grant a custom runner."},
            status=403,
        )
    try:
        body = await read_json_body(request)
    except Exception:
        body = None
    if (
        not isinstance(body, dict)
        or set(body) != {"approved", "expected_revision"}
        or body.get("approved") is not True
        or not isinstance(body.get("expected_revision"), str)
        or not body["expected_revision"]
    ):
        return web.json_response(
            {"error": "explicit approval and expected_revision are required"},
            status=400,
        )

    from gideon.engine.agents import runners
    from gideon.security import runner_grants

    definition = runners.catalog().get(request.match_info.get("id", ""))
    if definition is None or definition.source != "user":
        return web.json_response({"error": "custom runner not found"}, status=404)
    try:
        granted = runner_grants.grant(
            definition,
            principal=principal,
            expected_revision=body["expected_revision"],
        )
    except (OSError, TypeError, ValueError, PermissionError):
        logger.warning("custom runner owner grant could not be saved", exc_info=True)
        return web.json_response(
            {"error": "custom runner grant could not be saved"}, status=500
        )
    if not granted:
        return web.json_response(
            {"error": "runner definition changed; review the current definition again"},
            status=409,
        )
    tenant_key = _tenant_cache_key(principal)
    _readiness_cache.pop((tenant_key, definition.runtime_id), None)
    _discovery_cache.pop((tenant_key, definition.runtime_id), None)
    return web.json_response({"ok": True, "runner": definition.id}, status=200)


def register_runner_routes(app: web.Application) -> None:
    """Register owner runner-consent mutations alongside the runner catalog read."""
    app.router.add_post("/api/agent-runners/{id}/grant", api_agent_runner_grant)
