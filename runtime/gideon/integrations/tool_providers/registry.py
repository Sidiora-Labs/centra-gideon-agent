"""Tool provider registry."""

import logging
from typing import Any

from gideon.integrations.tool_providers.base import ToolDefinition, ToolProvider

logger = logging.getLogger(__name__)

_load_failures: list[dict[str, str]] = []


def record_failure(provider: str, error: str) -> None:
    """Record a tool-source load failure for operator surfacing (dedup by provider)."""
    _load_failures[:] = [f for f in _load_failures if f.get("provider") != provider]
    _load_failures.append({"provider": provider, "error": str(error)[:300]})


def get_load_failures() -> list[dict[str, str]]:
    """The recorded load failures (a copy)."""
    return list(_load_failures)


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


_providers: dict[str, ToolProvider] = {}


def register_provider(provider: ToolProvider) -> None:
    _providers[provider.name] = provider


def unregister_provider(name: str) -> None:
    _providers.pop(name, None)


def get_provider(name: str) -> ToolProvider | None:
    return _providers.get(name)


def list_providers() -> list[ToolProvider]:
    return list(_providers.values())


async def list_all_tools() -> list[ToolDefinition]:
    """Aggregate tools from all registered providers.

    A provider that raises while listing its tools is recorded as a load failure
    (operator-visible via :func:`get_load_failures`) rather than silently
    dropped, and the remaining providers still contribute.
    """
    all_tools: list[ToolDefinition] = []
    for prov in _providers.values():
        try:
            tools = await prov.list_tools()
            for t in tools:
                t.provider = prov.name
            all_tools.extend(tools)
        except Exception as exc:
            logger.warning(
                "Tool provider %r failed to list tools: %s",
                prov.name,
                exc,
                exc_info=True,
            )
            record_failure(prov.name, str(exc))
    return all_tools
