"Agent catalog projection, slug resolution and workflow reference discovery."

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_UNSAFE = re.compile(r"[^a-z0-9]+")

ACTIVATIONS = ("always", "conditional", "on-demand")


def slugify(name: str) -> str:
    """An agent's stable catalog key.

    Derived from the name rather than stored, so the catalog needs no migration when an agent is
    added: the derivation IS the key. Empty input yields `"agent"` rather than an empty string,
    because an empty key would silently merge every unnamed entry into one row.
    """
    stem = _UNSAFE.sub("-", (name or "").strip().lower()).strip("-")
    return stem or "agent"


@dataclass
class RosterEntry:
    """One catalog row.

    `name` is the config key (what `spawn(agent=...)` takes) and `label` is for display; they are
    separate fields because collapsing them is what makes a rename break a template.
    """

    slug: str
    name: str
    description: str = ""
    label: str = ""
    icon: str = ""
    capabilities: list[str] = field(default_factory=list)
    model_tier_hint: str = ""
    activation: str = "conditional"
    reserved: bool = False

    def to_dict(self) -> dict[str, Any]:
        record = {
            field_name: getattr(self, field_name)
            for field_name in (
                "slug",
                "name",
                "description",
                "label",
                "icon",
                "model_tier_hint",
                "activation",
                "reserved",
            )
        }
        record["capabilities"] = list(self.capabilities)
        return record


def _entry(name: str, profile: Any, reserved_names: frozenset[str]) -> RosterEntry:
    fields = {
        target: str(getattr(profile, source, "") or "")
        for target, source in (
            ("description", "description"),
            ("model_tier_hint", "model"),
        )
    }
    capabilities = {
        str(value)
        for group in ("tools", "skills")
        for value in (getattr(profile, group, None) or [])
    }
    reserved = name in reserved_names
    return RosterEntry(
        slug=slugify(name),
        name=name,
        label=name,
        icon="",
        capabilities=sorted(capabilities),
        activation="always" if reserved else "conditional",
        reserved=reserved,
        **fields,
    )


def catalog(agents: dict[str, Any] | None = None) -> list[RosterEntry]:
    from gideon.engine.agents.defaults import RESERVED_AGENT_NAMES

    if agents is None:
        from gideon.core.config.loader import AppConfig

        agents = AppConfig.load().agents or {}
    entries = (
        _entry(str(name), agents[name], RESERVED_AGENT_NAMES) for name in sorted(agents)
    )
    indexed = {}
    for entry in entries:
        if entry.slug not in indexed:
            indexed[entry.slug] = entry
    return list(map(indexed.__getitem__, sorted(indexed)))


def resolve(slug: str, agents: dict[str, Any] | None = None) -> RosterEntry | None:
    target = slugify(slug)
    return next((entry for entry in catalog(agents) if entry.slug == target), None)


def unresolved_slugs(
    referenced: list[str], agents: dict[str, Any] | None = None
) -> list[str]:
    known = {entry.slug for entry in catalog(agents)}
    requested = {slugify(name) for name in referenced if name}
    return sorted(requested.difference(known))


def referenced_slugs(spec: dict[str, Any]) -> list[str]:
    pending, found = [spec], set()
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(reversed(node))
        elif isinstance(node, dict):
            config = node.get("config")
            if isinstance(config, dict) and (
                name := str(config.get("agent", "") or "").strip()
            ):
                found.add(slugify(name))
            pending.extend(reversed(list(node.values())))
    return sorted(found)
