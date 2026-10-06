"""Declared scope of app-origin agent work."""

import json

AGENT_TEXT = "text"
AGENT_READ = "read"
AGENT_TOOLS = "tools"
AGENT_TIERS = (AGENT_TEXT, AGENT_READ, AGENT_TOOLS)


def agent_tier_covers(held: str, needed: str) -> bool:
    return (
        held in AGENT_TIERS
        and needed in AGENT_TIERS
        and AGENT_TIERS.index(held) >= AGENT_TIERS.index(needed)
    )


def declared_agent(value: object) -> tuple[str, str]:
    if value is None or value is False or value == "":
        return "", ""
    if isinstance(value, str) and value in AGENT_TIERS:
        return value, ""
    return "", json.dumps(value, default=str)


def capability_class(tier: str) -> str:
    return {AGENT_READ: "research", AGENT_TOOLS: "mutating"}.get(tier, "text")
