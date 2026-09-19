"""Translate action requests into the app router's authenticated invocation contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)


@dataclass(frozen=True)
class _RouteAction:
    app: str
    operation: str
    arguments: dict[str, Any]

    @classmethod
    def parse(cls, config: dict[str, Any]) -> _RouteAction:
        app, operation = (
            str(config.get(key, "") or "").strip() for key in ("app", "op")
        )
        if not app or not operation:
            raise ValueError("call-app-route action is missing 'app' and/or 'op'")
        arguments = config.get("args") or {}
        if not isinstance(arguments, dict):
            raise ValueError("call-app-route 'args' must be an object")
        return cls(app, operation, arguments)

    async def invoke(self) -> ActionResult:
        from gideon.integrations.tool_providers.app_routes import (
            RouteError,
            call_app_route,
            resolve_route,
        )

        try:
            target = resolve_route(self.app, self.operation, self.arguments)
        except RouteError as error:
            return ActionResult(
                False, error=error.agent_error.what, agent_error=error.agent_error
            )
        receipt = await call_app_route(target)
        details: dict = dict(stdout=receipt.output)
        if not receipt.success:
            details["agent_error"] = receipt.agent_error
            details["error"] = receipt.error or (
                receipt.agent_error.what if receipt.agent_error else ""
            )
        return ActionResult(success=bool(receipt.success), **details)


class CallAppRouteActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "call-app-route"

    @property
    def display_name(self) -> str:
        return "Call App Route"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        try:
            request = _RouteAction.parse(action_config)
        except ValueError as error:
            return ActionResult(False, error=str(error))
        return await request.invoke()


def create_provider(config: dict[str, Any] | None = None) -> CallAppRouteActionProvider:
    return CallAppRouteActionProvider()
