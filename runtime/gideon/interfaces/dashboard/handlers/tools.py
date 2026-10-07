"""Aggregated tools listing + invocation endpoints — surface and run tools
from all registered tool providers (the Tool entity)."""

import logging

from aiohttp import web

from gideon.core.http_request import read_json_body, string_field
from gideon.extensions.providers.failure_copy import relayed_failure_copy
from gideon.http_errors import json_error
from gideon.security.security import redact_credentials, redact_exfiltration_urls

logger = logging.getLogger(__name__)

_MCP_LIST_TIMEOUT_SECS = 5.0


def _sel():
    """Late-binding sel() — allows monkeypatching at parent package level."""
    import gideon.interfaces.dashboard.handlers as _pkg

    return _pkg.sel()


def _audit_toggle(
    request: web.Request, op: str, ok: bool, resources: str, error: str = ""
) -> None:
    """Audit a tool/provider enable-disable toggle (#45) — it changes the agent's
    available capability surface, a security-relevant state change. Best-effort."""
    try:
        _sel().log_api_access(
            caller=request.get("user", "dashboard"),
            operation=op,
            outcome="ok" if ok else "error",
            source="tools",
            resources=resources,
            error=error,
        )
    except Exception:
        pass


def _provider_trust_tiers() -> dict[str, str]:
    """provider name → supply-chain trust tier, for tool providers an APP contributes.

    #2627. The Tools page badge used to be a binary — ``platform`` when the provider was
    locked, ``built-in`` otherwise — so an installed community bundle (native kind, not
    locked) was labelled with the same word core's own first-party providers get. The
    install dialog had just disclosed "Unsigned — community tier" and the user consented
    to *that*; the page a user later audits from then said shipped-with-the-product. The
    fix is provenance on the wire, so the badge derives from where the provider came from
    rather than from whether it happens to be locked.

    A provider absent from this map is CORE (the cwd-coupled platform provider, the entity
    categories, anything registered by a factory rather than by an app) and the caller
    labels it ``builtin``. Every app-contributed provider is keyed twice — under the app
    name and under each live instance's own ``name`` — because an app that declares several
    provider instances registers them as ``{app}:{instance}``, which is the string the
    catalog tags its tools with and therefore the string the page groups by.

    Never raises: a broken registry read must not empty the Tools page, and the caller's
    ``builtin`` default is the pre-#2627 behaviour, so the worst case is the old label.
    """
    try:
        from gideon.extensions.apps.app_manager import trust_tier_of
        from gideon.extensions.apps.manager import list_apps
        from gideon.extensions.providers.registry import get_provider_registry

        tier_by_app = {
            str(app.get("name", "")): trust_tier_of(str(app.get("name", "")))
            for app in list_apps()
            if app.get("name")
        }
        out: dict[str, str] = {}
        for ext in get_provider_registry().list_by_type("tool"):
            tier = tier_by_app.get(ext.name)
            if not tier:
                continue
            out[ext.name] = tier
            instance = ext.provider_instance
            for provider in instance if isinstance(instance, list) else [instance]:
                inst_name = str(getattr(provider, "name", "") or "")
                if inst_name:
                    out[inst_name] = tier
        return out
    except Exception:
        logger.warning("Failed to resolve tool-provider trust tiers", exc_info=True)
        return {}


async def api_tools_list(request: web.Request) -> web.Response:
    """GET /api/tools — Return all tools from all active tool sources.

    There are exactly three sources, and they don't overlap:
    1. The session-coupled PLATFORM provider (filesystem + shell + tool_result_get).
       It's built per-runtime (cwd-bound) so it is NOT in the registry — enumerated
       directly here.
    2. The tool-provider REGISTRY (``list_all_tools``) — every registered in-process
       provider, which already includes ``gideon-core``
       (registered via their bundled app.json → the same ``InProcessMcpToolProvider``
       the native loop uses) plus the entity categories (memory/artifacts/…). The
       generic ``mcp`` provider is skipped here — source 3 emits external MCP tools
       labeled per-server.
    3. External MCP servers from the LIVE in-process client registry — probed, not
       static, so it can't be a registry read.

    Tools are deduplicated by ``(provider, name)`` pair (defence-in-depth; the three
    sources are already disjoint by construction).
    """
    from gideon.integrations.tool_providers.registry import (
        clear_load_failures,
        get_load_failures,
        list_all_tools,
        record_failure,
    )

    clear_load_failures()

    from gideon.integrations.tool_providers import tool_prefs

    disabled_keys = tool_prefs.load_disabled()
    disabled_provs = tool_prefs.load_disabled_providers()

    from gideon.integrations.tool_providers.groups import (
        CORE_GROUP,
        group_name_for_provider,
    )

    def _group_of(name: str, provider: str) -> str:
        return (
            CORE_GROUP
            if tool_prefs.is_locked(name)
            else group_name_for_provider(provider)
        )

    provider_tiers = _provider_trust_tiers()

    tools_out: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def _add(
        name: str,
        description: str,
        provider: str,
        parameters: dict,
        requires_approval: bool = True,
        risk_level: object = "safe",
        *,
        default_tier: str = "builtin",
    ) -> None:
        key = (provider, name)
        if key in seen or not name:
            return
        seen.add(key)
        locked = tool_prefs.is_locked(name)
        prov_off = provider in disabled_provs
        entry = {
            "name": name,
            "description": description,
            "provider": provider,
            "parameters": parameters,
            "requires_approval": requires_approval,
            "risk_level": getattr(risk_level, "value", risk_level) or "safe",
            "locked": locked,
            "providerDisabled": prov_off,
            "disabled": (not locked)
            and tool_prefs.is_disabled(provider, name, disabled_keys, disabled_provs),
            "group": _group_of(name, provider),
            "tier": provider_tiers.get(provider, default_tier),
        }
        if name.startswith(f"mcp/{provider}/"):
            entry["serverTool"] = name
        tools_out.append(entry)

    try:
        from gideon.engine.agents.native.builtin_tools import (
            create_platform_tools_provider,
        )

        _platform = create_platform_tools_provider()
        for t in await _platform.list_tools():
            _add(
                t.name,
                t.description,
                getattr(t, "provider", "") or _platform.name,
                t.parameters,
                getattr(t, "requires_approval", True),
                getattr(t, "risk_level", "safe"),
            )
    except Exception as exc:
        logger.warning("Failed to enumerate native platform tools", exc_info=True)
        record_failure("gideon-filesystem", str(exc))

    try:
        registry_tools = await list_all_tools(skip=("mcp",), skip_configured_mcp=True)
        for t in registry_tools:
            if t.provider == "mcp":
                continue
            _add(
                t.name,
                t.description,
                t.provider,
                t.parameters,
                t.requires_approval,
                getattr(t, "risk_level", "safe"),
                default_tier="" if t.name.startswith("mcp/") else "builtin",
            )
    except Exception as exc:
        logger.warning("Failed to list tools from registry", exc_info=True)
        record_failure("tool-registry", str(exc))

    return web.json_response({"tools": tools_out, "load_failures": get_load_failures()})


async def api_tool_invoke(request: web.Request) -> web.Response:
    """POST /api/tools/invoke — execute one tool through the Tool entity.

    Internal-only (loopback + X-Internal-Secret): used by zero-token cron
    scripts so a sandboxed subprocess gets the same MCP+native tool surface
    the agent has, without importing the in-process registry. Body:
    ``{"tool": str, "arguments": dict, "provider"?: str}``. Returns
    ``{ok, output, error}``.

    "The same surface the agent has" includes the user's tool preferences: a tool disabled
    on the Tools page is refused here with ``403 tool_disabled``, exactly as the runtime
    drops it at schema assembly. Core-locked tools and the locked platform provider are
    exempt (``tool_prefs.is_disabled`` handles that), so the primitives stay reachable.
    """
    from gideon.integrations.tool_providers.base import ToolProvider
    from gideon.integrations.tool_providers.registry import (
        get_ownership_refusals,
        list_providers,
        resolve_tool_catalog,
    )

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response(
            {"ok": False, "error": "invalid JSON body"}, status=400
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error": "body must be a JSON object"}, status=400
        )
    tool_name = body.get("tool")
    if not isinstance(tool_name, str) or not tool_name:
        return web.json_response(
            {"ok": False, "error": "tool name required"}, status=400
        )
    arguments = body.get("arguments") or {}
    if not isinstance(arguments, dict):
        return web.json_response(
            {"ok": False, "error": "arguments must be an object"}, status=400
        )

    app_name = request.get("app", "")
    if app_name:
        from gideon.extensions.apps.permissions import checker_for

        checker = checker_for(app_name)
        if checker is None or not checker.can_use_mcp_tool(str(body.get("tool") or "")):
            try:
                _sel().log_tool_invocation(
                    session_key=f"app:{app_name}",
                    agent="",
                    source="tool_invoke",
                    tool_name=str(body.get("tool") or ""),
                    tool_kind="",
                    outcome="denied",
                    error="tool not in app's declared mcpTools",
                )
            except Exception:
                pass
            return web.json_response(
                {
                    "ok": False,
                    "error": f"app {app_name!r} not permitted to invoke {tool_name!r} — declare it in permissions.mcpTools",  # noqa: E501
                },
                status=403,
            )

    provider_raw = body.get("provider")
    if provider_raw is not None and not isinstance(provider_raw, str):
        return web.json_response(
            {"ok": False, "error": "provider must be a string"}, status=400
        )
    provider_name = provider_raw or ""

    if tool_name.startswith("mcp/"):
        _prefix, separator, remainder = tool_name.partition("/")
        server, separator, raw_name = remainder.partition("/")
        if separator and raw_name:
            from gideon.integrations.tool_providers import tool_prefs

            if provider_name and provider_name != server:
                return web.json_response(
                    {
                        "ok": False,
                        "error": "MCP tool owner does not match its canonical name",
                    },
                    status=400,
                )
            if tool_prefs.is_disabled(server, tool_name):
                return json_error(
                    "tool_disabled",
                    message=(
                        f"{tool_name!r} is disabled — re-enable it on the Tools page to invoke it"
                    ),
                    status=403,
                )

    from gideon.engine.agents.native.builtin_tools import (
        PLATFORM_TOOL_NAMES,
        create_platform_tools_provider,
    )

    provider: ToolProvider | None = None
    sources = list_providers()
    if provider_name == "gideon-filesystem" or (
        not provider_name and tool_name in PLATFORM_TOOL_NAMES
    ):
        if tool_name not in PLATFORM_TOOL_NAMES:
            return web.json_response(
                {"ok": False, "error": f"tool not found: {tool_name}"}, status=404
            )
        from gideon.core.config.loader import workspace_root

        try:
            cwd = workspace_root().resolve(strict=True)
            if not cwd.is_dir():
                raise NotADirectoryError(str(cwd))
        except Exception:
            logger.warning(
                "Platform tool workspace could not be resolved", exc_info=True
            )
            return json_error("workspace_unresolved", status=503, ok=False)
        platform_provider = create_platform_tools_provider(cwd=cwd)
        sources.append(platform_provider)
    catalog = await resolve_tool_catalog(sources)
    provider = catalog.providers.get(tool_name)
    if provider_name and provider is not None and provider.name != provider_name:
        provider = None
    if provider is None:
        refusal = next(
            (
                item
                for item in get_ownership_refusals()
                if item.get("provider") == provider_name
                or item.get("tool") == tool_name
            ),
            None,
        )
        if refusal is not None:
            return web.json_response(
                {"ok": False, "error": "tool_ownership_refused", "refusal": refusal},
                status=409,
            )
        return web.json_response(
            {"ok": False, "error": f"tool not found: {tool_name}"}, status=404
        )

    _tool_def = next(
        (tool for tool in catalog.definitions if tool.name == tool_name), None
    )

    from gideon.integrations.tool_providers import tool_prefs

    _pkey = (getattr(_tool_def, "provider", "") or "") or provider.name
    if tool_prefs.is_disabled(_pkey, tool_name):
        try:
            _sel().log_tool_invocation(
                session_key=request.headers.get("X-Session-Key", "") or "internal",
                agent="",
                source="tool_invoke",
                tool_name=tool_name,
                tool_kind=provider.name,
                outcome="denied",
                error="tool is disabled by the user",
            )
        except Exception:  # noqa: BLE001 — an unaudited refusal is still a refusal
            pass
        return json_error(
            "tool_disabled",
            message=(
                f"{tool_name!r} is disabled — re-enable it on the Tools page to invoke it"
            ),
            status=403,
        )

    from gideon.integrations.mcp_core import (
        reset_current_session_key,
        set_current_session_key,
    )
    from gideon.integrations.tool_providers.arguments import (
        argument_refusal,
        refused_result,
    )

    reason = argument_refusal(
        tool_name, arguments, getattr(_tool_def, "parameters", {})
    )
    argument_result = refused_result(reason) if reason else None
    if argument_result is not None:
        return web.json_response(argument_result, status=400)
    token = set_current_session_key(
        request.headers.get("X-Session-Key", "") or "internal"
    )
    try:
        preflight_refusal = await provider.preflight(tool_name, arguments)
    except Exception:
        preflight_refusal = None
    finally:
        reset_current_session_key(token)
    if preflight_refusal is not None:
        return web.json_response(
            {
                "ok": False,
                "error": preflight_refusal.error,
                "metadata": preflight_refusal.metadata,
            },
            status=400,
        )

    from gideon.engine.task_modes import MAY_DESTROY, resolve_effective_risk

    _declared = getattr(_tool_def, "risk_level", "") if _tool_def is not None else ""
    _risk = resolve_effective_risk(_declared, tool_name, "", arguments)
    if _risk in MAY_DESTROY and body.get("confirm_risk") != "destructive":
        return json_error(
            "risk_confirmation_required",
            message=f"Confirm destructive risk before invoking {tool_name!r}",
            status=403,
        )

    from gideon.security.protected_folders import (
        call_protected_delete,
        provider_working_folder,
        sentence,
    )

    protected = call_protected_delete(
        _declared, tool_name, "", arguments, cwd=provider_working_folder(provider)
    )
    if protected:
        from gideon.security.approval_answer import OWNER, of_request

        if of_request(request).kind != OWNER:
            return json_error(
                "approval_required", message=sentence(protected), status=403
            )
    from gideon.engine.agents.native import builtin_tools

    delete_token = builtin_tools._CURRENT_APPROVED_DELETE.set(
        str(arguments.get("command", "")) if protected else ""
    )
    caller = request.headers.get("X-Session-Key", "") or "internal"
    try:
        if tool_name.startswith("mcp/"):
            from gideon.integrations.mcp_core import (
                reset_current_session_key,
                set_current_session_key,
            )

            session_token = set_current_session_key(caller)
            try:
                from gideon.integrations.tool_providers.registry import (
                    ConfiguredMcpToolProvider,
                )

                if isinstance(provider, ConfiguredMcpToolProvider):
                    result = await provider.invoke(
                        tool_name,
                        arguments,
                        expected_definition=getattr(
                            _tool_def, "mcp_definition_digest", ""
                        ),
                        expected_configuration=getattr(
                            _tool_def, "mcp_configuration_revision", ""
                        ),
                    )
                else:
                    result = await provider.invoke(tool_name, arguments)
            finally:
                reset_current_session_key(session_token)
        else:
            if tool_name == "inbox_list":
                from gideon.integrations.inbox_reach import (
                    _REQUEST_READER,
                    reader_of_request,
                )

                inbox_reader_token = _REQUEST_READER.set(
                    reader_of_request(request, request.app["state"])
                )
                try:
                    result = await provider.invoke(tool_name, arguments)
                finally:
                    _REQUEST_READER.reset(inbox_reader_token)
            else:
                result = await provider.invoke(tool_name, arguments)
    except Exception as exc:
        _sel().log_tool_invocation(
            session_key=caller,
            agent="",
            source="tool_invoke",
            tool_name=tool_name,
            tool_kind=provider.name,
            outcome="error",
            error=str(exc)[:200],
            metadata={"risk": _risk},
        )
        return web.json_response(
            {"ok": False, "error": relayed_failure_copy(exc)}, status=500
        )
    finally:
        builtin_tools._CURRENT_APPROVED_DELETE.reset(delete_token)

    _sel().log_tool_invocation(
        session_key=caller,
        agent="",
        source="tool_invoke",
        tool_name=tool_name,
        tool_kind=provider.name,
        outcome="completed" if result.success else "error",
        metadata={"risk": _risk},
    )
    out, _ = redact_exfiltration_urls(result.output or "")
    out, _ = redact_credentials(out)
    err, _ = redact_exfiltration_urls(result.error or "")
    err, _ = redact_credentials(err)
    return web.json_response({"ok": bool(result.success), "output": out, "error": err})


async def api_tools_toggle(request: web.Request) -> web.Response:
    """POST /api/tools/toggle — enable/disable a native-provider tool.

    Body ``{"provider": str, "name": str, "enabled": bool}``. Writes
    ``~/.gideon/tool_prefs.json``, which BOTH execution paths honor: the native
    runtime drops disabled tools at schema assembly (the model cannot see or call them),
    and ``POST /api/tools/invoke`` refuses them with ``403 tool_disabled``. This docstring
    used to promise only the first, accurately — and the toggle's UI presented itself as
    the tool's on/off switch while a second path executed it anyway (#437).

    Core-locked tools are rejected (4xx). MCP tools use ``/api/mcp/toggle-tool`` (which
    writes mcp.json) — the page routes by provider.
    """
    from gideon.integrations.tool_providers import tool_prefs
    from gideon.integrations.tool_providers.registry import list_all_tools

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response(
            {"ok": False, "error": "invalid JSON body"}, status=400
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error": "body must be a JSON object"}, status=400
        )
    provider = str(body.get("provider", "")).strip()
    name = string_field(body, "name")
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        return web.json_response(
            {"ok": False, "error": "enabled must be a boolean"}, status=400
        )
    if not name:
        return web.json_response({"ok": False, "error": "name is required"}, status=400)
    if name.startswith("mcp/") or provider == "mcp":
        return web.json_response(
            {
                "ok": False,
                "error": "MCP tools must use /api/mcp/toggle-tool with server and raw tool name",
            },
            status=400,
        )
    if name not in {
        t.name for t in await list_all_tools(skip=("mcp",), skip_configured_mcp=True)
    }:
        return web.json_response(
            {"ok": False, "error": f"unknown tool {name!r}"}, status=404
        )
    result = tool_prefs.set_enabled(provider, name, enabled)
    _audit_toggle(
        request,
        "tools.toggle",
        result.get("ok", False),
        f"{provider}:{name}={'on' if enabled else 'off'}",
        result.get("error", ""),
    )
    if not result.get("ok"):
        return web.json_response(result, status=409 if result.get("locked") else 400)
    return web.json_response(result)


async def api_providers_toggle(request: web.Request) -> web.Response:
    """POST /api/tools/provider-toggle — enable/disable a whole NATIVE tool provider.

    Body ``{"provider": str, "enabled": bool}``. Writes
    ``tool_prefs.json``'s ``disabledProviders``; the runtime skips a disabled
    provider's entire toolset. The locked platform provider is rejected (409). MCP
    servers use ``/api/mcp/toggle`` (mcp.json); the Tools page routes by kind.
    """
    from gideon.integrations.tool_providers import tool_prefs

    try:
        body = await read_json_body(request)
    except Exception:
        return web.json_response(
            {"ok": False, "error": "invalid JSON body"}, status=400
        )
    if not isinstance(body, dict):
        return web.json_response(
            {"ok": False, "error": "body must be a JSON object"}, status=400
        )
    provider = str(body.get("provider", "")).strip()
    enabled = body.get("enabled", True)
    if not isinstance(enabled, bool):
        return web.json_response(
            {"ok": False, "error": "enabled must be a boolean"}, status=400
        )
    if not provider:
        return web.json_response(
            {"ok": False, "error": "provider is required"}, status=400
        )
    result = tool_prefs.set_provider_enabled(provider, enabled)
    _audit_toggle(
        request,
        "tools.provider_toggle",
        result.get("ok", False),
        f"{provider}={'on' if enabled else 'off'}",
        result.get("error", ""),
    )
    if not result.get("ok"):
        return web.json_response(result, status=409 if result.get("locked") else 400)
    return web.json_response(result)


async def api_tools_savings(request: web.Request) -> web.Response:
    """GET /api/tools/savings — the TokenJuice savings (counterfactual) summary.

    Read-only aggregate from ``~/.gideon/tokenjuice_savings.json`` (Context Economy
    §1.3): estimated tokens saved by output projection, the top compressor, and a
    per-compressor breakdown. Tokens are estimated (``chars/4``, flagged ``estimated``) —
    this is the *savings* ledger, not authoritative spend metering. Never raises (an
    absent/corrupt file returns an empty summary)."""
    from gideon.integrations.tool_providers import savings

    return web.json_response(savings.summary())


async def api_tool_groups(request: web.Request) -> web.Response:
    """GET /api/tools/groups — the tool-GROUP partition (Context Economy §5).

    Groups are *derived* from the registered tool providers, so this reports the
    same partition the native runtime assembles: one entry per group with its tool
    count, whether it's always-on (``core``), whether its declared capability
    resolves (``offerable``), and the per-surface activation defaults.

    Read-only by design. Activation is **per-session runtime state** (seeded from
    the defaults, changed by the agent's ``reset_tools``), not a stored preference
    — so there is nothing here to toggle. What the user configures is the feature
    flag (``tools.groups_enabled``) and the per-surface defaults
    (``tools.group_defaults``), both via the config API.
    """
    from gideon.integrations.tool_providers import groups as groups_mod
    from gideon.integrations.tool_providers.registry import list_all_tools

    defs: list = []
    try:
        defs = [
            t
            for t in await list_all_tools(skip=("mcp",), skip_configured_mcp=True)
            if t.provider != "mcp"
        ]
    except Exception:
        logger.warning("Failed to list tools for the group partition", exc_info=True)
    try:
        from gideon.engine.agents.native.builtin_tools import (
            create_platform_tools_provider,
        )

        defs = list(await create_platform_tools_provider().list_tools()) + defs
    except Exception:
        logger.warning("Failed to enumerate platform tools for groups", exc_info=True)

    configured_defaults = groups_mod.configured_group_defaults()
    surfaces = {
        surface: list(configured_defaults.get(surface, []))
        for surface in ("chat", "background", "loops", "orchestration")
    }
    out = []
    for group in groups_mod.partition(defs):
        out.append(
            {
                "name": group.name,
                "display": group.display,
                "alwaysOn": group.always_on,
                "toolCount": len(group.tools),
                "tools": list(group.tools),
                "capability": group.capability,
                "offerable": groups_mod.offerable(group),
                "instructions": group.instructions,
            }
        )
    return web.json_response(
        {
            "enabled": groups_mod.groups_enabled(),
            "groups": out,
            "surfaceDefaults": surfaces,
        }
    )
