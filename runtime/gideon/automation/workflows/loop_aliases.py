"""Read-time workflow aliases and container stream identity projection."""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

KIND_TO_TEMPLATE: dict[str, str] = {
    "general": "general-project",
    "goal": "goal-pursuit-open-ended",
    "code": "code-project",
    "design": "design-project",
    "research": "deep-research",
}

TOOL_TO_TEMPLATE: dict[str, str] = {
    "loop_create_general": "general-project",
    "loop_create_goal": "goal-pursuit-open-ended",
    "loop_create_code": "code-project",
    "loop_create_design": "design-project",
    "loop_create_research": "deep-research",
    "loop_start": "general-project",
}

VARIANT_HINTS: dict[tuple[str, str], str] = {
    ("goal", "verifiable"): "goal-pursuit-verifiable",
    ("goal", "open_ended"): "goal-pursuit-open-ended",
    ("goal", "open-ended"): "goal-pursuit-open-ended",
    ("goal", "monitor"): "goal-pursuit-monitor",
}


def resolve_kind(
    kind: str, *, variant: str = "", has_verify_command: bool = False
) -> str:
    normalized = (kind or "").strip().lower()
    if not normalized:
        return ""
    choices = (
        lambda: (
            VARIANT_HINTS.get((normalized, variant.strip().lower()))
            if variant
            else None
        ),
        lambda: (
            "goal-pursuit-verifiable"
            if normalized == "goal" and has_verify_command
            else None
        ),
    )
    for candidate in choices:
        target = candidate()
        if target:
            return target
    target = KIND_TO_TEMPLATE.get(normalized)
    if target is not None:
        return target
    logger.debug("no template alias for loop kind %r", kind)
    return ""


def resolve_tool(tool_name: str) -> str:
    """The template a legacy loop chat-tool name meant, or ""."""
    return TOOL_TO_TEMPLATE.get((tool_name or "").strip().lower(), "")


def aliased_kinds() -> list[str]:
    """Every legacy kind that still resolves. Shrinks to empty at the endgame."""
    return sorted(KIND_TO_TEMPLATE)


def alias_manifest() -> dict[str, object]:
    snapshot: dict[str, object] = {
        name: dict(table)
        for name, table in (("kinds", KIND_TO_TEMPLATE), ("tools", TOOL_TO_TEMPLATE))
    }
    snapshot["variants"] = dict(
        (":".join(key), value) for key, value in VARIANT_HINTS.items()
    )
    snapshot.update(
        one_way=True,
        note="Read-time aliases for legacy loop references. Deleted wholesale at the Phase-4 endgame; never written to.",
    )
    return snapshot


_KEY_PREFIXES = ("workflow:run:", "workflow:", "loop:", "run:")


def base_container(key: str) -> str:
    raw = (key or "").strip()
    prefix = next((prefix for prefix in _KEY_PREFIXES if raw.startswith(prefix)), "")
    return raw[len(prefix) :]


def keys_equivalent(left: str, right: str) -> bool:
    identities = tuple(map(base_container, (left, right)))
    return all(identities) and identities[0] == identities[1]
