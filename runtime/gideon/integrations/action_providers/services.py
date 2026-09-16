"""Service accessor for native action providers.

bash/webhook/run-script actions are self-contained, but the native action
providers (notify / send-message / create-task / invoke-agent) must reach
in-process services — ConsoleState (notifications / channel send), the tasks
registry, the DelegationSupervisor — without importing the dashboard package
(layering) and without each provider re-discovering globals.

Mirrors ``gideon.engine.hooks.set_global_hook_store`` / ``get_global_hook_store``:
the dashboard wires a :class:`ActionServices` at startup; providers fetch it
lazily and return an error result (never raise) if it is unset, so a misordered
startup fails loudly in tests rather than silently no-opping.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gideon.engine.subagent import DelegationSupervisor
    from gideon.interfaces.dashboard.state import ConsoleState


@dataclass
class ActionServices:
    """Handles native action providers need. Wired once at dashboard startup."""

    state: "ConsoleState"
    spawn_background: Callable[[Awaitable[Any]], Any]
    subagents: "DelegationSupervisor | None" = None
    workflows: Any = None


_services: "ActionServices | None" = None
_service_lock = RLock()


def set_action_services(svc: "ActionServices") -> None:
    global _services
    with _service_lock:
        _services = svc


def get_action_services() -> "ActionServices | None":
    with _service_lock:
        current = _services
    return current


def validate_spawn_cwd(cwd: str) -> str:
    if cwd:
        try:
            from gideon.core.config.loader import AppConfig
            from gideon.engine.subagent import validate_cwd

            policy = AppConfig.load().agent
            validation = validate_cwd(cwd, policy.subagent_cwd_allowed_roots)
            return validation[1] or ""
        except Exception:
            pass
    return ""
