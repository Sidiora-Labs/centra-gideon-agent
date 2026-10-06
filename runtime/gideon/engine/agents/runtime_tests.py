"""Explicit, single-flight ACP runtime checks for the settings Test action."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

from gideon.engine.agents.provider import ReadinessStatus

logger = logging.getLogger(__name__)
_inflight: dict[tuple[str, str, str], asyncio.Task[dict[str, Any]]] = {}


def _tenant_key(tenant: Any) -> str:
    if hasattr(tenant, "kind"):
        return str(getattr(tenant, "tenant", "") or "self-hosted")
    return str(tenant or "self-hosted")


async def test_runtime(
    runtime_id: str, entry: Any, *, tenant: Any = None
) -> dict[str, Any]:
    """Start the selected ACP runtime once, capture its discovery, and retire it."""
    from gideon.engine.agents import runners

    definition = runners.definition_for_runtime(runtime_id)
    if definition is None:
        return _result(
            ReadinessStatus(False, "error", "No runner definition is available.")
        )
    if not runners.owner_grant_allowed(definition, tenant=tenant):
        return _result(
            ReadinessStatus(
                False,
                "needs_owner_approval",
                "Owner approval is required for this runner definition.",
            )
        )
    options_fingerprint = hashlib.sha256(
        repr((entry.name, entry.type, entry.model, entry.options)).encode("utf-8")
    ).hexdigest()
    key = (_tenant_key(tenant), runtime_id, options_fingerprint)
    current = _inflight.get(key)
    if current is None or current.done():
        current = asyncio.create_task(_run_once(runtime_id, entry, tenant=tenant))
        _inflight[key] = current

        def clear(done: asyncio.Task[dict[str, Any]]) -> None:
            if _inflight.get(key) is done:
                _inflight.pop(key, None)

        current.add_done_callback(clear)
    return dict(await asyncio.shield(current))


async def _run_once(runtime_id: str, entry: Any, *, tenant: Any) -> dict[str, Any]:
    from gideon.engine.agents import runners
    from gideon.engine.agents.registry import get_agent_provider_class
    from gideon.integrations.llm.registry import get_default_registry

    definition = runners.definition_for_runtime(runtime_id)
    if definition is None or not runners.owner_grant_allowed(definition, tenant=tenant):
        return _result(
            ReadinessStatus(
                False,
                "needs_owner_approval",
                "Owner approval is required for this runner definition.",
            )
        )
    try:
        current_entry = get_default_registry().get_entry(runtime_id)
    except Exception:
        return _result(
            ReadinessStatus(False, "error", "ACP runtime is no longer configured.")
        )
    if (
        current_entry.type != "acp_agent"
        or current_entry.model != entry.model
        or current_entry.options != entry.options
    ):
        return _result(
            ReadinessStatus(
                False, "stale", "ACP runtime configuration changed; refresh and retry."
            )
        )
    provider = get_agent_provider_class("acp")
    if provider is None:
        return _result(ReadinessStatus(False, "error", "ACP provider is unavailable."))
    options = dict(entry.options or {})
    command = options.get("command")
    if (
        entry.name != runtime_id
        or entry.type != "acp_agent"
        or not isinstance(command, list)
        or not command
        or any(
            not isinstance(part, str) or not part or "\0" in part for part in command
        )
    ):
        return _result(
            ReadinessStatus(
                False, "error", "ACP runtime command configuration is invalid."
            )
        )
    options.update(runtime_id=runtime_id, runtime_label=_runtime_label(runtime_id))
    try:
        from gideon.integrations.llm.acp_agent import AcpAgentProvider

        tested_provider = get_default_registry().build(runtime_id, model=entry.model)
        if not isinstance(tested_provider, AcpAgentProvider):
            raise TypeError("Runtime Test requires an ACP agent provider")
        readiness, snapshot = await asyncio.wait_for(
            tested_provider.explicit_self_test_with_snapshot(), timeout=120
        )
        agents = []
        permission_modes: list[str] = []
        if readiness.ready:
            discovered_agents = tested_provider.agents_from_snapshot(
                options, snapshot, record_capabilities=False
            )
            agents = [
                {
                    "id": agent.id,
                    "name": agent.name,
                    "runtime": agent.runtime,
                    "description": agent.description,
                    "provider_agent": agent.provider_agent,
                    "reasoning_effort": agent.reasoning_effort,
                    "models": list(agent.models),
                    "supported_efforts": list(agent.supported_efforts),
                }
                for agent in discovered_agents
            ]
            permission_modes = _permission_modes(options)
        return {
            **_result(readiness),
            "agents": agents,
            "permission_modes": permission_modes,
        }
    except Exception as exc:  # noqa: BLE001 - settings reports a safe test result
        logger.info("Explicit ACP test failed for %s", runtime_id, exc_info=True)
        from gideon.extensions.providers.failure_copy import relayed_failure_copy

        state = "timeout" if isinstance(exc, TimeoutError) else "error"
        return _result(ReadinessStatus(False, state, relayed_failure_copy(exc)))


def _runtime_label(runtime_id: str) -> str:
    name = runtime_id.split(":", 1)[-1]
    return " ".join(
        part.capitalize() for part in name.replace("_", "-").split("-") if part
    )


def _permission_modes(options: dict[str, Any]) -> list[str]:
    from gideon.integrations.acp.dialect import ZedAdapterDialect, get_dialect

    if isinstance(get_dialect(options.get("dialect")), ZedAdapterDialect):
        return ["default", "acceptEdits", "plan", "dontAsk", "bypassPermissions"]
    return []


def _result(status: ReadinessStatus) -> dict[str, Any]:
    return {
        "ready": bool(status.ready),
        "state": status.state,
        "detail": status.detail,
        "login_command": status.login_command,
        "agents": [],
        "permission_modes": [],
    }
