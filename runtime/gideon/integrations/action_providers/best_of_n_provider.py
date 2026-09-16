"""Action request and outcome mapping for the shared sampling engine."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)


@dataclass(frozen=True)
class _SamplingRequest:
    prompt: str
    count: int
    criteria: str

    @classmethod
    def read(cls, config: dict[str, Any]) -> _SamplingRequest:
        prompt = str(config.get("prompt") or "").strip()
        if not prompt:
            raise ValueError("best-of-n requires a non-empty 'prompt'")
        try:
            count = int(config.get("n") or 3)
        except (TypeError, ValueError):
            raise ValueError(
                f"best-of-n: 'n' must be a number, got {config['n']!r}"
            ) from None
        return cls(prompt, count, str(config.get("criteria") or ""))


def _sampling_result(envelope: dict[str, Any]) -> ActionResult:
    selected = envelope.get("winner") is not None
    reason = (
        ""
        if selected
        else str(envelope.get("note") or "no candidate: every sampling call failed")
    )
    return ActionResult(
        success=selected,
        stdout=json.dumps(envelope, ensure_ascii=False),
        error=reason,
    )


class BestOfNActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "best-of-n"

    @property
    def display_name(self) -> str:
        return "Best-of-N Sampling"

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        from gideon.integrations.sampling import best_of_n

        try:
            request = _SamplingRequest.read(action_config)
        except ValueError as exc:
            return ActionResult(False, error=str(exc))
        try:
            envelope = await best_of_n(request.prompt, request.count, request.criteria)
        except Exception as exc:
            return ActionResult(
                False, error=f"best-of-n sampling failed: {type(exc).__name__}: {exc}"
            )
        return _sampling_result(envelope)


def create_provider(config: dict[str, Any] | None = None) -> BestOfNActionProvider:
    return BestOfNActionProvider()
