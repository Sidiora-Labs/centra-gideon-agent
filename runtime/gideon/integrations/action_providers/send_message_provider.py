"""Resolve owner/channel delivery for a rendered native action message."""

from __future__ import annotations

import logging
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

logger = logging.getLogger(__name__)


def _redacted_text(text: str) -> str:
    try:
        from gideon.security.security import (
            redact_credentials,
            redact_exfiltration_urls,
        )

        for redact in (redact_exfiltration_urls, redact_credentials):
            text, _ = redact(text)
    except Exception:
        logger.debug("send-message: redaction unavailable", exc_info=True)
    return text


@dataclass(frozen=True)
class _MessageDelivery:
    text: str
    title: str
    channel: str
    user: str

    def notify(self, state: Any) -> ActionResult:
        try:
            state.notify(
                notification_kinds.INFO, self.title or "Agent message", self.text
            )
        except Exception as error:
            return ActionResult(
                False, error=f"send-message: no channel + notify failed: {error}"
            )
        return ActionResult(
            True, stdout="no channel provider; delivered as notification"
        )

    async def send(self, state: Any, delivery: Any) -> ActionResult:
        try:
            destination = self.channel
            if not destination:
                recipient = self.user or getattr(state, "owner_id", "") or ""
                if not recipient:
                    return ActionResult(
                        False, error="send-message: no channel/user and no owner to DM"
                    )
                destination = await delivery.open_dm(recipient)
            if not destination:
                return ActionResult(
                    False, error="send-message: could not resolve a delivery target"
                )
            rendered = (
                "\n".join((f"*{self.title}*", self.text)) if self.title else self.text
            )
            await delivery.deliver_text(destination, rendered)
        except Exception as error:
            return ActionResult(False, error=f"send-message failed: {error}")
        return ActionResult(True, stdout=f"sent: {self.text[:80]}")


class SendMessageActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "send-message"

    @property
    def display_name(self) -> str:
        return "Send Message"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        text = render_template(action_config.get("text_template", ""), ctx).strip()
        if not text:
            return ActionResult(
                False, error="send-message hook is missing 'text_template'"
            )
        title = (action_config.get("title") or "").strip()
        services = get_action_services()
        if services is None:
            return ActionResult(
                False,
                error="send-message hook: services unavailable (startup not wired)",
            )
        request = _MessageDelivery(
            _redacted_text(text),
            title,
            (action_config.get("channel") or "").strip(),
            (action_config.get("user") or "").strip(),
        )
        state = services.state
        target = getattr(state, "channel_delivery", None)
        return (
            request.notify(state)
            if target is None
            else await request.send(state, target)
        )


def create_provider(config: dict[str, Any] | None = None) -> SendMessageActionProvider:
    return SendMessageActionProvider()
