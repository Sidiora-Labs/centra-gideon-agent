"""Correlate ACP tool results with the procedural-memory outcome vocabulary."""

from __future__ import annotations

import logging
from typing import Any

from gideon.integrations.acp.types import EVENT_TOOL_CALL, EVENT_TOOL_RESULT

logger = logging.getLogger(__name__)
MAX_OUTCOMES = 200


def _vocabulary() -> frozenset[str]:
    from gideon.cognition.memory_service import PROCEDURAL_OUTCOMES

    return PROCEDURAL_OUTCOMES


class ToolOutcomeAccumulator:
    def __init__(self) -> None:
        self._names: dict[str, str] = {}
        self._outcomes: list[tuple[str, str]] = []

    def begin_turn(self) -> None:
        self._names = {}
        self._outcomes = []

    def observe(self, event: Any) -> None:
        kind = str(getattr(event, "kind", "") or "")
        call_id = str(getattr(event, "tool_call_id", "") or "")
        if kind == EVENT_TOOL_RESULT:
            self._record_result(call_id, getattr(event, "tool_meta", None))
        elif kind == EVENT_TOOL_CALL:
            name = str(getattr(event, "title", "") or "").strip()
            if call_id and name:
                self._names[call_id] = name

    def _record_result(self, call_id: str, metadata: object) -> None:
        name = self._names.pop(call_id, None)
        if name is None:
            return
        failed = isinstance(metadata, dict) and metadata.get("ok") is False
        outcome = ("success", "failed")[failed]
        if outcome not in _vocabulary():
            logger.warning(
                "ACP result %r is not a procedural outcome; discarded", outcome
            )
        elif len(self._outcomes) < MAX_OUTCOMES:
            self._outcomes.append((name, outcome))

    def drain(self) -> list[tuple[str, str]]:
        batch, self._outcomes = self._outcomes, []
        return batch


class AcpToolOutcomesMixin:
    @property
    def _outcome_accumulator(self) -> ToolOutcomeAccumulator:
        if getattr(self, "_acp_tool_outcomes", None) is None:
            self._acp_tool_outcomes = ToolOutcomeAccumulator()
        return self._acp_tool_outcomes

    def drain_tool_outcomes(self) -> list[tuple[str, str]]:
        return self._outcome_accumulator.drain()
