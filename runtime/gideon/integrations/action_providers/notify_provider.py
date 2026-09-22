"""Build a native dashboard notice from rendered action fields."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.services import get_action_services
from gideon.integrations.action_providers.template import render_template
from gideon.workspace import notification_kinds

_ALLOWED_KINDS = frozenset(
    (
        notification_kinds.INFO,
        notification_kinds.SUCCESS,
        notification_kinds.WARNING,
        notification_kinds.ERROR,
    )
)


@dataclass(frozen=True)
class _Notice:
    title: str
    body: str
    kind: str

    @classmethod
    def prepare(cls, config: dict[str, Any], ctx: ActionContext, title: str) -> _Notice:
        heading = "[test] " + title if bool((ctx.payload or {}).get("test")) else title
        body = render_template(config.get("body_template", ""), ctx)
        requested = (config.get("kind") or "info").strip().lower()
        return cls(heading, body, requested if requested in _ALLOWED_KINDS else "info")

    def publish(self, state: Any) -> ActionResult:
        try:
            state.notify(self.kind, self.title, self.body)
        except Exception as error:
            return ActionResult(False, error=f"notify failed: {error}")
        return ActionResult(True, stdout=f"notified: {self.title[:80]}")


class NotifyActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "notify"

    @property
    def display_name(self) -> str:
        return "Dashboard Notification"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        title = render_template(action_config.get("title_template", ""), ctx).strip()
        if not title:
            return ActionResult(False, error="notify hook is missing 'title_template'")
        notice = _Notice.prepare(action_config, ctx, title)
        services = get_action_services()
        if services is not None:
            return notice.publish(services.state)
        return ActionResult(
            False, error="notify hook: services unavailable (startup not wired)"
        )


def create_provider(config: dict[str, Any] | None = None) -> NotifyActionProvider:
    return NotifyActionProvider()
