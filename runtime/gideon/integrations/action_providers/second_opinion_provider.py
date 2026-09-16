"""Translate a stalled action into the proposer engine's single handoff contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.template import render_template

_MAX_TIMEOUT_SECS = 1800.0
_DEFAULT_TIMEOUT_SECS = 300.0


def _str_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        cleaned = value.strip()
        return (cleaned,) if cleaned else ()
    if not isinstance(value, (list, tuple)):
        return ()
    strings = map(str, value)
    return tuple(item for item in strings if item.strip())


@dataclass(frozen=True)
class _HandoffRequest:
    arguments: dict[str, Any]

    @classmethod
    def read(cls, config: dict[str, Any], ctx: ActionContext) -> _HandoffRequest:
        rendered = {
            key: render_template(str(config.get(key) or ""), ctx).strip()
            for key in ("goal", "stuck_at")
        }
        if not all(rendered.values()):
            raise ValueError("second-opinion requires 'goal' and 'stuck_at'")
        origin = str(config.get("origin_runner") or "").strip()
        if not origin:
            raise ValueError(
                "second-opinion requires 'origin_runner' — without the runner that stalled the different-runner exclusion cannot be enforced"
            )
        payload = ctx.payload if isinstance(ctx.payload, dict) else {}
        try:
            seconds = float(config.get("timeout_secs") or _DEFAULT_TIMEOUT_SECS)
        except (ValueError, TypeError):
            seconds = _DEFAULT_TIMEOUT_SECS
        arguments = {
            **rendered,
            "ask": render_template(str(config.get("ask") or ""), ctx).strip(),
            "origin_runner": origin,
            "workspace": str(
                config.get("workspace")
                or payload.get("workspace")
                or payload.get("cwd")
                or ""
            ).strip(),
            "session_key": str(
                config.get("session_key") or payload.get("session_key") or ""
            ).strip(),
            "sandbox": str(config.get("sandbox") or payload.get("sandbox") or "none"),
            "consumer": str(config.get("consumer") or ctx.event or ""),
            "brief_dir": str(config.get("brief_dir") or "").strip(),
            "require_health": bool(config.get("require_health", True)),
            "timeout_secs": max(1.0, min(seconds, _MAX_TIMEOUT_SECS)),
        }
        for key in (
            "attempts",
            "files_touched",
            "required_capabilities",
            "binding_order",
        ):
            arguments[key] = _str_tuple(config.get(key))
        return cls(arguments)


def _handoff_result(outcome: Any) -> ActionResult:
    return ActionResult(
        success=outcome.accepted,
        stdout=json.dumps(outcome.to_dict(), indent=2),
        error="" if outcome.accepted else outcome.rejection,
    )


class SecondOpinionActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "second-opinion"

    @property
    def display_name(self) -> str:
        return "Second Opinion (hand off to a different runner)"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.cognition.proposer.service import run_second_opinion

        try:
            request = _HandoffRequest.read(action_config, ctx)
        except ValueError as exc:
            return ActionResult(False, error=str(exc))
        return _handoff_result(await run_second_opinion(**request.arguments))
