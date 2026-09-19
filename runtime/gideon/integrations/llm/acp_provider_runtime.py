"""Shared turn control and launch planning for ACP provider facades."""

from __future__ import annotations

import asyncio
import logging
import shutil
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gideon.integrations.acp.errors import AcpError
from gideon.integrations.acp.types import STOP_REASON_CANCELLED, STOP_REASON_END_TURN
from gideon.integrations.llm.base import CancelOutcome

logger = logging.getLogger(__name__)


def capability_names(payload: Any) -> frozenset[str]:
    if not isinstance(payload, dict):
        return frozenset()
    return frozenset(str(key) for key, value in payload.items() if value is not False)


async def relay_events(
    provider, produce: Callable, text: str, stamp: Callable | None = None
) -> AsyncIterator:
    accumulator = provider._outcome_accumulator
    accumulator.begin_turn()
    async for raw in produce(text):
        if stamp is not None:
            stamp(raw)
        translated = provider._to_llm_event(raw)
        accumulator.observe(translated)
        yield translated


async def cancel_turn(
    endpoint, send_method: str, timeout: float, *, tolerate_wait_errors: bool = False
) -> CancelOutcome:
    if not endpoint.has_active_turn():
        return "no_turn"
    try:
        await getattr(endpoint, send_method)()
    except AcpError:
        logger.debug("ACP cancellation request failed", exc_info=True)
        return "error"
    if timeout <= 0:
        return "acked"
    try:
        reason = await endpoint.wait_turn_done(timeout=timeout)
    except Exception as error:
        if isinstance(error, TimeoutError) or tolerate_wait_errors:
            return "timeout"
        raise
    return (
        "acked"
        if reason in (STOP_REASON_CANCELLED, STOP_REASON_END_TURN)
        else "timeout"
    )


async def close_quietly(close: Callable) -> None:
    try:
        await close()
    except Exception:
        logger.debug("ACP probe resource cleanup failed", exc_info=True)


@dataclass(frozen=True)
class ProbePlan:
    options: dict
    command: list[str]

    @classmethod
    def from_options(cls, options: dict) -> ProbePlan | None:
        value = options.get("command")
        return (
            cls(options, list(map(str, value)))
            if isinstance(value, list) and value
            else None
        )

    @property
    def timeout(self) -> float:
        return float(self.options.get("probe_timeout_secs") or 45)

    def preflight(self):
        from gideon.engine.agents.provider import ReadinessStatus
        from gideon.integrations.acp.cli_resolve import is_npx_fallback, resolve_node_ge

        problem = ""
        if not shutil.which(self.command[0]):
            problem = f"'{self.command[0]}' not found on PATH"
        elif is_npx_fallback(self.command) and not resolve_node_ge():
            problem = (
                "ACP adapter is not installed and cannot be auto-provisioned: "
                "no Node >= 20 found (the adapter needs it). Install Node >= 20, "
                "install the adapter, or set the bundle's *_ACP_BIN override."
            )
        else:
            requirement = self.options.get("requires_executable")
            if isinstance(requirement, dict):
                label = str(requirement.get("label") or "engine")
                resolved = str(requirement.get("path") or "").strip() or shutil.which(
                    label
                )
                if not resolved:
                    variable = str(requirement.get("env_var") or "").strip()
                    hint = f" (set {variable} or install it)" if variable else ""
                    problem = (
                        f"ACP adapter present but its engine CLI '{label}' was not found{hint} "
                        "— the adapter delegates the model turn to it, so the runtime cannot serve without it."
                    )
        return ReadinessStatus(False, "not_found", problem) if problem else None

    def failure(self, error: Exception):
        from gideon.engine.agents.provider import ReadinessStatus

        configured = self.options.get("login_command")
        login = (
            list(map(str, configured))
            if isinstance(configured, list) and configured
            else self.command[:1]
        )
        text = str(error).lower()
        if any(
            word in text
            for word in (
                "log in",
                "login",
                "logged in",
                "authenticat",
                "unauthor",
                "sign in",
            )
        ):
            return ReadinessStatus(
                False, "needs_login", f"agent requires sign-in: {error}", login
            )
        if isinstance(error, TimeoutError):
            return ReadinessStatus(
                False,
                "timeout",
                f"handshake timed out after {self.timeout:.0f}s (cold start may be slow — retry; sign in only if the agent needs auth)",
                login,
            )
        return ReadinessStatus(False, "error", f"handshake failed: {error}")

    async def readiness(self, provider_type):
        from gideon.engine.agents.provider import ReadinessStatus

        refused = self.preflight()
        if refused is not None:
            return refused
        provider = provider_type(
            command=self.command,
            cwd=self.options.get("cwd"),
            env=self.options.get("env") or {},
            dialect=self.options.get("dialect"),
        )
        timeout = self.timeout
        closed = False
        try:
            await asyncio.wait_for(provider.start(), timeout=timeout)
            names = sorted(provider.declared_capabilities)
            await provider.shutdown()
            closed = True
            return ReadinessStatus(
                True, "ready", f"initialize OK (caps: {', '.join(names) or 'none'})"
            )
        except Exception as error:
            return self.failure(error)
        finally:
            if not closed:
                await close_quietly(provider.shutdown)

    async def snapshot(self) -> dict:
        from gideon.core.config.loader import workspace_root
        from gideon.integrations.acp.client import CLIENT_NAME, CLIENT_VERSION
        from gideon.integrations.acp.dialect import get_dialect
        from gideon.integrations.acp.session import AcpConnection

        dialect = get_dialect(self.options.get("dialect"))
        workspace = workspace_root()
        connection = await asyncio.wait_for(
            AcpConnection.spawn(
                command=self.command,
                work_dir=workspace,
                dialect=dialect,
                extra_env=self.options.get("env") or {},
            ),
            timeout=self.timeout,
        )
        try:
            operations = (
                (
                    connection.initialize,
                    {
                        "protocolVersion": dialect.protocol_version(),
                        "clientInfo": dialect.client_info(
                            client_name=CLIENT_NAME, client_version=CLIENT_VERSION
                        ),
                    },
                ),
                (connection.new_session, {"cwd": str(workspace), "mcpServers": []}),
            )
            for operation, parameters in operations:
                await operation(parameters, timeout=self.timeout)
            return dict(connection.last_session_new_snapshot or {})
        finally:
            await close_quietly(connection.close)


def launch_arguments(entry, session_key: str | None, overrides: dict) -> dict:
    from gideon.integrations.llm.registry import ProviderResolutionError

    options = dict(entry.options or {})
    argv = options.get("command")
    if not isinstance(argv, list) or not argv:
        raise ProviderResolutionError(
            f"acp_agent provider entry {entry.name!r} requires a non-empty options.command list (got {type(argv).__name__})"
        )

    def text(key: str) -> str:
        return str(overrides.get(key) or "").strip()

    def choice(key: str, default: str, fallback: str = "") -> str:
        return text(key) or str(options.get(default) or "").strip() or fallback

    def path(value) -> Path | None:
        return Path(str(value)) if value else None

    def mapping(key: str, convert: Callable) -> dict:
        raw = options.get(key)
        return (
            {str(k): convert(v) for k, v in raw.items()}
            if isinstance(raw, dict)
            else {}
        )

    channel = text("channel_id") or options.get("channel_id")
    dialect = options.get("dialect")
    return {
        "command": list(map(str, argv)),
        "cwd": path(text("cwd") or options.get("cwd")),
        "env": mapping("env", str),
        "model": text("model") or entry.model,
        "agent_name": choice("agent", "agent_name"),
        "capability_flags": mapping("capability_flags", bool),
        "session_key": session_key,
        "channel_id": str(channel) if channel else None,
        "sandbox_mode": str(options.get("sandbox_mode") or "auto"),
        "sandbox": choice("sandbox", "sandbox", "none"),
        "session_files_dir": path(options.get("session_files_dir")),
        "dialect": str(dialect) if dialect else None,
        "mode": choice("acp_mode", "mode"),
        "reasoning_effort": text("reasoning_effort_override"),
        "unattended": bool(overrides.get("unattended", False)),
        "runtime_id": entry.name,
    }


def pooled_permission_mode(mode: str, unattended: bool) -> str:
    from gideon.integrations.acp.permission_authority import sanitize_mode

    decision = sanitize_mode(mode, unattended=unattended)
    if not decision.downgraded:
        return decision.mode
    logger.warning("ACP pooled permission mode clamped: %s", decision.reason)
    try:
        from gideon.security.sel import sel

        sel().log_api_access(
            caller="acp:permission_authority",
            operation="mode_change:clamped_to_host_authority",
            outcome="downgraded",
            resources=f"pooled requested={decision.requested} effective={decision.mode}",
        )
    except Exception:
        logger.warning("SEL audit failed for pooled ACP mode clamp", exc_info=True)
    return decision.mode
