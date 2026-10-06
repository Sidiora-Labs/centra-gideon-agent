"""Tool provider registry."""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable

from gideon.integrations.tool_providers.base import ToolDefinition, ToolProvider

logger = logging.getLogger(__name__)

_load_failures: list[dict[str, str]] = []


def record_failure(provider: str, error: str) -> None:
    """Record a tool-source load failure for operator surfacing (dedup by provider)."""
    _load_failures[:] = [f for f in _load_failures if f.get("provider") != provider]
    _load_failures.append({"provider": provider, "error": str(error)[:300]})


def get_load_failures() -> list[dict[str, Any]]:
    """The recorded load failures (a copy)."""
    return [*list(_load_failures), *get_ownership_refusals()]


def clear_load_failures() -> None:
    """Reset the failure list — called at the start of each catalog build."""
    _load_failures.clear()


def create_native_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-core`` tool surface.

    Returns an in-process provider wrapping ``mcp_core`` directly — the same
    working path the native loop uses.
    """
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider()


def create_automation_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-automation`` tool surface — in-process over
    ``mcp_automation`` (§4's `automation_*` namespace: create/list/update/pause/resume/run/
    history/delete over the unified trigger store)."""
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_automation",
        provider_name="gideon-automation",
        display="Gideon Automations",
    )


def create_computer_use_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-computer-use`` tool surface — in-process over
    ``computer_use.tools`` (`DCU-4`'s seven ``computer_*`` tools).

    Registered for the same reason every other aggregated category is: ``mcp_core``'s ACP
    surface and the in-process catalog must not diverge
    (``tests/test_native_tool_categories.py`` pins that in both directions), so a tool an ACP
    CLI can call must also be one the Tools page, the manifest and the offline reference name.
    A surface reachable by one agent and invisible to the operator is half a feature.

    The provider changes NO authority. ``computer_use.tools`` is the thin shim either way — it
    forwards to the gateway's ``/api/computer-use/dispatch`` over ``mcp_core._post``, so
    in-process invocation is a loopback round trip rather than a second, shorter path into the
    dispatch. That is deliberate: a branch on "am I inside the gateway" would give the one
    security-sensitive transport in this package two code paths, and only one of them would be
    exercised by whichever surface the next test happened to use. ``InProcessMcpToolProvider``
    runs ``_call_tool`` in an executor thread, so the loopback cannot stall the event loop.
    """
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.computer_use.tools",
        provider_name="gideon-computer-use",
        display="Gideon Computer Use",
    )


def create_artifacts_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-artifacts`` tool surface — in-process
    over ``mcp_artifacts`` (the Artifacts entity tool group)."""
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_artifacts",
        provider_name="gideon-artifacts",
        display="Gideon Artifacts",
    )


def create_workflows_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-workflows`` tool surface — in-process
    over ``mcp_workflows`` (the v2 workflow engine's 19-tool chat surface)."""
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_workflows",
        provider_name="gideon-workflows",
        display="Gideon Workflows",
    )


def create_prompts_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-prompts`` tool surface — in-process
    over ``mcp_prompts`` (render the user's saved, parameterized Prompts)."""
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_prompts",
        provider_name="gideon-prompts",
        display="Gideon Prompts",
    )


def create_memory_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-memory`` tool surface — in-process
    over ``mcp_memory`` (persistent lessons + on-demand recall)."""
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_memory",
        provider_name="gideon-memory",
        display="Gideon Memory",
    )


def create_subagents_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the ``gideon-subagents`` tool surface — in-process
    over ``mcp_subagents`` (spawn + track background subagents)."""
    from gideon.engine.agents.native.tools import InProcessMcpToolProvider

    return InProcessMcpToolProvider(
        module="gideon.integrations.mcp_subagents",
        provider_name="gideon-subagents",
        display="Gideon Subagents",
    )


def create_code_map_provider(config: dict[str, Any] | None = None) -> ToolProvider:
    """Extension factory for the code-map tool surface — symbol lookup over the
    tree-sitter codebase index (Context-Economy §5.5), replacing several grep/read
    round-trips with one call. Registered as ``workflows-tools`` so its group derives
    to ``workflows``; fails soft to grep/read when no index exists."""
    from gideon.integrations.tool_providers.code_map import CodeMapToolProvider

    return CodeMapToolProvider()


@dataclass(frozen=True)
class ProviderRegistration:
    provider: ToolProvider
    owner_type: str
    owner: str
    instance_id: str
    status_callback: Callable[[dict[str, Any]], None] | None = None

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.owner_type, self.owner, self.instance_id)

    @property
    def label(self) -> str:
        suffix = f"@{self.instance_id}" if self.instance_id != self.owner else ""
        return f"{self.owner_type}:{self.owner}{suffix}"


@dataclass
class ToolCatalog:
    definitions: list[ToolDefinition]
    providers: dict[str, ToolProvider]
    owners: dict[str, str]
    refusals: list[dict[str, Any]]


class ConfiguredMcpToolProvider(ToolProvider):
    """Expose one configured MCP server through the canonical tool catalog."""

    def __init__(self, server: str) -> None:
        self.server = server

    @property
    def name(self) -> str:
        return self.server

    @property
    def display_name(self) -> str:
        return f"MCP: {self.server}"

    async def list_tools(self, *, cached_only: bool = False) -> list[ToolDefinition]:
        from gideon.integrations.mcp_client import get_mcp_client_registry
        from gideon.security import mcp_read_only_trust
        from gideon.integrations.tool_providers.base import risk_from_annotations

        registry = get_mcp_client_registry()
        if registry is None:
            return []
        spec = registry._specs.get(self.server)
        if not isinstance(spec, dict):
            return []
        conn = registry.get(self.server)
        if conn is None:
            return []
        disabled = spec.get("disabledTools", [])
        disabled_names = {name for name in disabled if isinstance(name, str)} if isinstance(disabled, list) else set()
        try:
            if cached_only:
                from gideon.integrations.mcp_client import McpToolSpec
                from gideon.integrations.mcp_discovery import _probe_cache, definition_seal
                cached = _probe_cache.get(self.server)
                advertised = [McpToolSpec(
                    name=row["name"], description=str(row.get("description") or ""),
                    input_schema=row.get("inputSchema") or {},
                    annotations=row.get("annotations") or {},
                ) for row in cached.tools] if cached and cached.status == "ok" and cached.definition_revision == definition_seal(self.server, spec) else []
            else:
                advertised = await conn.list_tools()
        except Exception:
            logger.debug("MCP server %s could not list agent tools", self.server, exc_info=True)
            return []

        counts: dict[str, int] = {}
        for item in advertised:
            name = getattr(item, "name", None)
            if isinstance(name, str) and name.strip() and not any(ord(char) < 32 for char in name):
                counts[name] = counts.get(name, 0) + 1
        definitions: list[ToolDefinition] = []
        for item in advertised:
            tool_name = getattr(item, "name", None)
            if (
                not isinstance(tool_name, str)
                or not tool_name.strip()
                or any(ord(char) < 32 for char in tool_name)
                or counts.get(tool_name) != 1
                or tool_name in disabled_names
            ):
                continue
            raw_schema = getattr(item, "input_schema", None)
            definitions.append(
                ToolDefinition(
                    name=f"mcp/{self.server}/{tool_name}",
                    description=str(getattr(item, "description", "") or ""),
                    provider=self.server,
                    parameters=raw_schema if isinstance(raw_schema, dict) else raw_schema,
                    requires_approval=not (getattr(item, "annotations", {}).get("readOnlyHint") is True and mcp_read_only_trust.believes(self.server, item)),
                    risk_level=risk_from_annotations(getattr(item, "annotations", {}), trusted=mcp_read_only_trust.believes(self.server, item)),
                    mcp_definition_digest=mcp_read_only_trust.digest(item),
                    mcp_configuration_revision=mcp_read_only_trust.configuration_revision(self.server),
                    annotations=getattr(item, "annotations", {}) if isinstance(getattr(item, "annotations", None), dict) else {},
                )
            )
        from gideon.integrations.tool_providers.portable_schema import offered_tool_definitions

        return offered_tool_definitions(definitions, provider=self.server)

    async def preflight(self, tool_name: str, arguments: dict[str, Any]):
        from gideon.integrations.mcp_client import get_mcp_client_registry, _coerce_args_to_schema
        from gideon.integrations.mcp_core import get_current_session_key
        from .arguments import argument_refusal, refused_result
        registry = get_mcp_client_registry()
        if registry is None:
            return None
        conn = registry.get(self.server, str(get_current_session_key() or ""))
        if conn is None:
            return None
        raw = tool_name.removeprefix(f"mcp/{self.server}/")
        spec = next((t for t in conn._tools if t.name == raw), None)
        if spec is None:
            return None
        reason = argument_refusal(tool_name, _coerce_args_to_schema(arguments, spec.input_schema), spec.input_schema, types=True)
        return refused_result(reason) if reason else None

    async def invoke(self, tool_name: str, arguments: dict[str, Any], *, expected_definition: str = "", expected_configuration: str = "", expected_read_authority: bool = False):
        from gideon.integrations.tool_providers.base import ToolResult
        if not expected_definition or not expected_configuration:
            return ToolResult(False, error="MCP tool was not run: its offered definition and configuration are required", metadata={"effect_state": "not_started"})
        from gideon.integrations.mcp_client import get_mcp_client_registry
        from gideon.integrations.mcp_core import get_current_session_key
        from gideon.integrations.tool_providers.base import ToolResult

        prefix = f"mcp/{self.server}/"
        if not isinstance(tool_name, str) or not tool_name.startswith(prefix):
            return ToolResult(False, error="MCP tool is not owned by this server")
        raw_name = tool_name[len(prefix):]
        if not raw_name:
            return ToolResult(False, error="MCP tool name is required")
        registry = get_mcp_client_registry()
        if registry is None:
            return ToolResult(False, error="MCP support is unavailable")
        spec = registry._specs.get(self.server)
        if not isinstance(spec, dict):
            return ToolResult(False, error="MCP server is not configured or enabled")
        disabled = spec.get("disabledTools", [])
        if isinstance(disabled, list) and raw_name in disabled:
            return ToolResult(False, error="MCP tool is disabled")
        session_key = get_current_session_key()
        conn = registry.get(self.server, str(session_key or ""))
        if conn is None:
            return ToolResult(False, error="MCP server is not configured or enabled")
        try:
            success, output = await conn.call_tool(raw_name, arguments, expected_definition=expected_definition, expected_configuration=expected_configuration, expected_read_authority=expected_read_authority)
            return ToolResult(bool(success), output=output if success else "", error="" if success else output, metadata={"effect_state": "not_started"} if not success and output.startswith("MCP tool was not run:") else {})
        except Exception as exc:
            return ToolResult(False, error=str(exc)[:300])


_providers: dict[str, ToolProvider] = {}
_registrations: dict[int, ProviderRegistration] = {}
_ownership_refusals: list[dict[str, Any]] = []
_catalog_lock = asyncio.Lock()
_mcp_provider_instances: dict[str, ConfiguredMcpToolProvider] = {}


def register_provider(
    provider: ToolProvider,
    *,
    owner_type: str = "core",
    owner: str = "",
    instance_id: str = "",
    status_callback: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Register a provider without allowing a different owner to replace it.

    Tool-name claims are finalized atomically by :func:`resolve_tool_catalog`, which
    can inspect the provider's asynchronous declarations before exposing any of them.
    """
    if owner_type not in {"core", "app", "mcp"}:
        return {"accepted": False, "reason": "invalid_owner_type"}
    provider_name = str(getattr(provider, "name", "") or "").strip()
    owner_name = str(owner or (provider_name if owner_type != "app" else ""))
    instance = str(
        instance_id or getattr(provider, "instance_id", "") or owner_name or provider_name
    )
    if not provider_name or not owner_name or not instance:
        return {"accepted": False, "reason": "missing_provider_identity"}

    identity = (owner_type, owner_name, instance)
    for prior in list(_registrations.values()):
        if (
            prior.identity == identity
            and prior.provider.name == provider_name
            and prior.provider is not provider
        ):
            _registrations.pop(id(prior.provider), None)
    _providers.clear()
    for prior in sorted(
        _registrations.values(),
        key=lambda r: (_owner_order(r.owner_type), r.owner, r.instance_id, r.provider.name),
    ):
        _providers.setdefault(prior.provider.name, prior.provider)

    registration = ProviderRegistration(
        provider=provider,
        owner_type=owner_type,
        owner=owner_name,
        instance_id=instance,
        status_callback=status_callback,
    )
    _providers.setdefault(provider_name, provider)
    _registrations[id(provider)] = registration
    result = {
        "accepted": None,
        "reason": "pending_tool_ownership_check",
        "provider": provider_name,
        "owner": owner_name,
        "instance_id": instance,
    }
    if status_callback is not None:
        status_callback(result)
    return result


def unregister_provider(
    name: str,
    *,
    owner_type: str | None = None,
    owner: str | None = None,
    instance_id: str | None = None,
) -> None:
    candidates = [
        record
        for record in _registrations.values()
        if record.provider.name == name
        and (
            owner_type is None
            or (
                record.owner_type == owner_type
                and record.owner == str(owner or "")
                and (not instance_id or record.instance_id == str(instance_id))
            )
        )
    ]
    for record in candidates:
        _registrations.pop(id(record.provider), None)
    remaining = next(
        (record.provider for record in _registrations.values() if record.provider.name == name),
        None,
    )
    if remaining is None:
        _providers.pop(name, None)
    else:
        _providers[name] = remaining


def get_provider(name: str) -> ToolProvider | None:
    candidates = sorted(
        (record for record in _registrations.values() if record.provider.name == name),
        key=lambda r: (_owner_order(r.owner_type), r.owner, r.instance_id),
    )
    return candidates[0].provider if candidates else _providers.get(name)


def _sync_mcp_server_providers() -> None:
    """Register the configured MCP servers as trusted, canonical tool owners."""
    try:
        from gideon.integrations.mcp_client import get_mcp_client_registry

        client_registry = get_mcp_client_registry()
        desired = (
            {
                name
                for name in client_registry._specs
                if isinstance(name, str) and name and "/" not in name
            }
            if client_registry is not None
            else set()
        )
    except Exception:
        logger.debug("MCP tool provider synchronization failed", exc_info=True)
        desired = set()

    for server in set(_mcp_provider_instances) - desired:
        unregister_provider(
            server, owner_type="mcp", owner=server, instance_id=server
        )
        _mcp_provider_instances.pop(server, None)
    for server in sorted(desired):
        provider = _mcp_provider_instances.get(server)
        if provider is None:
            provider = ConfiguredMcpToolProvider(server)
            _mcp_provider_instances[server] = provider
        register_provider(
            provider, owner_type="mcp", owner=server, instance_id=server
        )


def list_providers() -> list[ToolProvider]:
    _sync_mcp_server_providers()
    return [
        record.provider
        for record in sorted(
            _registrations.values(),
            key=lambda r: (_owner_order(r.owner_type), r.owner, r.instance_id, r.provider.name),
        )
    ]


def _owner_order(owner_type: str) -> int:
    return {"core": 0, "mcp": 1, "app": 2}.get(owner_type, 3)


def get_ownership_refusals() -> list[dict[str, Any]]:
    """Return structured ownership refusals from the latest catalog evaluation."""
    return [dict(item) for item in _ownership_refusals]


def _namespace_refusal(owner_type: str, names: list[str]) -> str:
    """Keep external MCP tool names in their reserved canonical namespace."""
    if owner_type == "mcp":
        return ""
    return "mcp_namespace_reserved" if any(name.startswith("mcp/") for name in names) else ""


def _registration_for(provider: ToolProvider) -> ProviderRegistration:
    registered = _registrations.get(id(provider))
    if registered is not None:
        return registered
    from gideon.integrations.mcp_delegated import McpDelegatedToolProvider

    if isinstance(provider, McpDelegatedToolProvider):
        session = str(provider.session_key or "shared")
        return ProviderRegistration(provider, "mcp", provider.name, session)
    name = str(getattr(provider, "name", "") or "")
    return ProviderRegistration(provider, "core", name, name)


async def resolve_tool_catalog(
    providers: list[ToolProvider] | None = None,
    *, skip: tuple[str, ...] = (),
    skip_configured_mcp: bool = False,
    cached_mcp: bool = False,
) -> ToolCatalog:
    """Build the one deterministic accepted tool set used by agent and dashboard.

    A provider is admitted only when every declaration is unique and owned by it.
    Core providers are ordered before apps; apps are ordered by stable app and
    instance identity, so restart order cannot alter the winner.
    """
    _sync_mcp_server_providers()
    async with _catalog_lock:
        selected = (
            [record.provider for record in _registrations.values()]
            if providers is None
            else list(providers)
        )
        unique: list[ToolProvider] = []
        seen: set[int] = set()
        for provider in selected:
            if provider.name in skip or (skip_configured_mcp and isinstance(provider, ConfiguredMcpToolProvider)):
                continue
            if id(provider) not in seen:
                unique.append(provider)
                seen.add(id(provider))

        records: list[ProviderRegistration] = []
        for provider in unique:
            records.append(_registration_for(provider))
        records.sort(
            key=lambda r: (
                _owner_order(r.owner_type),
                r.owner,
                r.instance_id,
                r.provider.name,
            )
        )

        reserved: dict[str, str] = {}
        try:
            from gideon.engine.agents.native.builtin_tools import PLATFORM_TOOL_NAMES

            reserved.update({name: "core:gideon-filesystem" for name in PLATFORM_TOOL_NAMES})
        except Exception:
            logger.debug(
                "platform tool names unavailable for ownership reservation",
                exc_info=True,
            )

        definitions: list[ToolDefinition] = []
        accepted_providers: dict[str, ToolProvider] = {}
        accepted_owners: dict[str, str] = {}
        refusals: list[dict[str, Any]] = []
        statuses: list[tuple[ProviderRegistration, dict[str, Any]]] = []
        for record in records:
            provider = record.provider
            try:
                declared = list(await provider.list_tools(cached_only=cached_mcp)) if isinstance(provider, ConfiguredMcpToolProvider) else list(await provider.list_tools())
            except Exception as exc:
                record_failure(provider.name, str(exc))
                continue
            names = [str(getattr(tool, "name", "") or "").strip() for tool in declared]
            reason = ""
            conflict = ""
            if any(not name for name in names):
                reason = "provider_declared_empty_tool_name"
            elif len(names) != len(set(names)):
                reason = "provider_declared_duplicate_tool_name"
            elif namespace_reason := _namespace_refusal(record.owner_type, names):
                reason = namespace_reason
                conflict = next((name for name in names if name.startswith("mcp/")), "")
            else:
                for name in names:
                    claimed = reserved.get(name)
                    platform_owner = record.owner_type == "core" and claimed in (
                        f"core:{provider.name}",
                        f"core:{record.owner}",
                    )
                    if claimed and claimed.startswith("core:") and not platform_owner:
                        reason, conflict = "tool_name_owned_by_core", name
                        break
                    if name in accepted_providers:
                        reason, conflict = "tool_name_already_owned", name
                        break
            if reason:
                refusal = {
                    "provider": provider.name,
                    "owner": record.owner,
                    "owner_type": record.owner_type,
                    "instance_id": record.instance_id,
                    "accepted": False,
                    "reason": reason,
                }
                if conflict:
                    refusal["tool"] = conflict
                    refusal["existing_owner"] = reserved.get(conflict) or accepted_owners.get(
                        conflict, ""
                    )
                if reason == "mcp_namespace_reserved":
                    refusal["error"] = (
                        f"Tool provider {provider.name!r} was refused because the mcp/ "
                        "namespace belongs to trusted MCP providers."
                    )
                elif reason in {"tool_name_already_owned", "tool_name_owned_by_core"}:
                    refusal["error"] = (
                        f"Tool provider {provider.name!r} was refused because tool "
                        f"{conflict!r} is already owned by {refusal.get('existing_owner')!r}."
                    )
                elif reason == "provider_declared_duplicate_tool_name":
                    refusal["error"] = (
                        f"Tool provider {provider.name!r} was refused because it declared "
                        "the same tool name more than once."
                    )
                else:
                    refusal["error"] = (
                        f"Tool provider {provider.name!r} was refused because it declared "
                        "an empty tool name."
                    )
                refusals.append(refusal)
                statuses.append((record, refusal))
                continue

            label = record.label
            for tool in declared:
                tool.provider = provider.name
                definitions.append(tool)
                accepted_providers[tool.name] = provider
                accepted_owners[tool.name] = label
                reserved.setdefault(tool.name, label)
            statuses.append(
                (
                    record,
                    {
                        "provider": provider.name,
                        "owner": record.owner,
                        "owner_type": record.owner_type,
                        "instance_id": record.instance_id,
                        "accepted": True,
                        "reason": "",
                    },
                )
            )

        _ownership_refusals[:] = refusals
        for record, status in statuses:
            if record.status_callback is not None:
                try:
                    record.status_callback(dict(status))
                except Exception:
                    logger.debug("tool provider ownership status callback failed", exc_info=True)
        return ToolCatalog(definitions, accepted_providers, accepted_owners, refusals)


async def list_all_tools(*, skip: tuple[str, ...] = (), skip_configured_mcp: bool = False) -> list[ToolDefinition]:
    """Aggregate tools from all registered providers.

    A provider that raises while listing its tools is recorded as a load failure
    (operator-visible via :func:`get_load_failures`) rather than silently
    dropped, and the remaining providers still contribute.
    """
    catalog = await resolve_tool_catalog(skip=skip, skip_configured_mcp=skip_configured_mcp)
    return catalog.definitions
