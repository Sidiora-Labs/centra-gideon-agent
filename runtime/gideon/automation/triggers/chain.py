"""Select completion subscribers and carry bounded cascade provenance."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterator

from gideon.automation.triggers.provider import armable
from gideon.automation.triggers.routing import routed

logger = logging.getLogger(__name__)
MAX_CHAIN_DEPTH = 3
DEPTH_KEY = "chain_depth"
PATH_KEY = "chain_path"


def _subscribers(store: Any, field: str, source: str) -> Iterator[Any]:
    for trigger in armable(routed(store)):
        if trigger.kind == "run_completed" and trigger.enabled:
            specification = trigger.spec if isinstance(trigger.spec, dict) else {}
            expected = str(specification.get(field, "") or "").strip()
            if expected and expected == source:
                yield trigger


@dataclass(frozen=True)
class CascadeTrail:
    visited: tuple[str, ...]
    depth: int

    @classmethod
    def read(cls, payload: dict[str, Any]) -> CascadeTrail:
        path = payload.get(PATH_KEY)
        visited = tuple(map(str, path)) if isinstance(path, list) else ()
        try:
            depth = int(payload.get(DEPTH_KEY, 0) or 0)
        except (TypeError, ValueError):
            depth = 0
        return cls(visited, depth)

    def refusal(self, identity: str) -> str:
        chain = " → ".join(self.visited)
        if identity in self.visited:
            return (
                f"chain cycle: {identity} already fired in this chain "
                f"({chain}), so it is refused rather than looping"
            )
        if self.depth >= MAX_CHAIN_DEPTH:
            return (
                f"chain depth limit reached ({MAX_CHAIN_DEPTH}); {identity} is refused. "
                f"Chain so far: {chain or 'unknown'}"
            )
        return ""

    def payload(self, source: str, target: Any) -> dict[str, Any]:
        visited = list(self.visited)
        if source not in visited:
            visited.append(source)
        return {
            "trigger_id": target.id,
            "trigger_name": target.name,
            "kind": "run_completed",
            "source_trigger_id": source,
            DEPTH_KEY: self.depth + 1,
            PATH_KEY: visited,
        }


def chain_triggers(store: Any, *, source_id: str) -> list[Any]:
    return list(_subscribers(store, "source_trigger", source_id))


def chain_triggers_for_def(store: Any, *, source_def: str) -> list[Any]:
    return list(_subscribers(store, "source_def", source_def)) if source_def else []


def chain_refusal(payload: dict[str, Any], *, next_id: str) -> str:
    return CascadeTrail.read(payload).refusal(next_id)


def chain_payload(
    source_payload: dict[str, Any], *, source_id: str, trigger: Any
) -> dict[str, Any]:
    return CascadeTrail.read(source_payload).payload(source_id, trigger)


def next_fires(
    store: Any,
    *,
    source_id: str,
    source_payload: dict[str, Any] | None = None,
    source_def: str = "",
) -> tuple[list[tuple[Any, dict[str, Any]]], list[dict[str, str]]]:
    payload = dict(source_payload or {})
    waiting = list(chain_triggers(store, source_id=source_id))
    if source_def:
        already_matched = {trigger.id for trigger in waiting}
        waiting.extend(
            trigger
            for trigger in chain_triggers_for_def(store, source_def=source_def)
            if trigger.id not in already_matched
        )
    accepted, rejected = [], []
    for trigger in waiting:
        refusal = chain_refusal(payload, next_id=trigger.id)
        if refusal:
            logger.info("run_completed %s refused: %s", trigger.id, refusal)
            rejected.append(dict(trigger_id=trigger.id, reason=refusal))
        else:
            accepted.append(
                (trigger, chain_payload(payload, source_id=source_id, trigger=trigger))
            )
    return accepted, rejected
