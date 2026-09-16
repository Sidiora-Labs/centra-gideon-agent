"""Convert a resolved runtime binding to the catalog identity used by a turn."""

from __future__ import annotations


def resolve_agent_id(
    agent: str | None, provider_kind: str | None, provider_agent: str | None
) -> str:
    runtime = (provider_kind or "").strip()
    family, separator, _ = runtime.partition(":")
    if separator and family == "acp":
        persona = (provider_agent or "").strip()
        pieces = (runtime, persona) if persona else (runtime,)
        return "/".join(pieces)
    identity = agent or provider_agent or ""
    return identity.strip()
