"Deterministic entity grounding and topic extraction for workflow plans."

from __future__ import annotations

import re
from typing import Any, Callable

EntityResolver = Callable[[str], list[dict]]

IDENTITY_GUARD = (
    "Use exactly this resolved identity. Do not substitute a different entity unless a tool result "
    "explicitly disproves it."
)

NO_PATTERN_MATCH_PROHIBITION = (
    "Do not pattern-match narrative to an unresolved name — if an entity was not resolved, treat "
    "it as unknown rather than assuming who or what it is."
)

_STOP = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "for",
        "to",
        "of",
        "in",
        "on",
        "with",
        "my",
        "me",
        "i",
        "it",
        "this",
        "that",
        "is",
        "are",
        "be",
        "do",
        "does",
        "from",
        "by",
        "at",
        "as",
        "about",
        "into",
        "over",
        "then",
        "so",
        "we",
        "our",
        "us",
        "you",
        "your",
        "up",
        "out",
        "how",
        "what",
        "why",
        "when",
        "where",
        "which",
        "please",
        "want",
        "need",
        "make",
        "set",
        "get",
        "run",
    }
)


def extract_topics(goal: str, *, limit: int = 6) -> list[str]:
    topics, seen = [], set()
    for match in re.finditer(r"[a-z0-9][a-z0-9\-]{2,}", (goal or "").lower()):
        token = match.group()
        if token not in seen and token not in _STOP:
            seen.add(token)
            topics.append(token)
            if len(topics) >= limit:
                break
    return topics


def resolve_entities(
    goal: str, resolver: EntityResolver | None
) -> tuple[list[dict], bool]:
    """Resolve the entities a goal names. Returns `(entities, degraded)`.

    `degraded` is True when a resolver was supposed to run and could not produce a resolved identity
    — no graph wired, or the lookup found nothing for a goal that clearly names something. It is the
    flag the emitted node carries so a downstream stage knows it is working from a name, not a
    resolved identity, and a reviewer can see WHY the preamble is thin.
    """
    if resolver is None:
        return [], True
    try:
        entities = resolver(goal) or []
    except Exception:
        return [], True
    return entities, not entities


def build_preamble_node(
    goal: str, resolver: EntityResolver | None
) -> dict[str, Any] | None:
    entities, degraded = resolve_entities(goal, resolver)
    payload = {
        "resolved_entities": entities,
        "topics": extract_topics(goal),
        "degraded": degraded,
        "guard": IDENTITY_GUARD,
    }
    if not (payload["resolved_entities"] or payload["topics"]):
        return None
    if degraded or _entity_heavy(entities):
        payload.update(prohibition=NO_PATTERN_MATCH_PROHIBITION)
    return {"kind": "transform", "id": "ground", "config": {"expr": payload}}


def _entity_heavy(entities: list[dict]) -> bool:
    kinds = (str(entity.get("entity_type", "")).lower() for entity in entities)
    return any(kind in _HEAVY_ENTITY_TYPES for kind in kinds)


def prepend_preamble(root: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(root, dict) or not node:
        return root
    children = root.get("children")
    if root.get("kind") == "sequence" and isinstance(children, list):
        return {**root, "children": [node, *children]}
    return {"kind": "sequence", "id": "root", "children": [node, root]}


_HEAVY_ENTITY_TYPES = frozenset(
    {"person", "organization", "org", "company", "product", "place", "asset", "ticker"}
)
