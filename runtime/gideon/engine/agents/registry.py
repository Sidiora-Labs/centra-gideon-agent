"""Ordered runtime registration with exact identities overriding family defaults."""

from __future__ import annotations

from gideon.engine.agents.provider import AgentProvider


class _RuntimeCatalog:
    def __init__(self):
        self.entries: dict[str, type[AgentProvider]] = {}

    def register(self, identifier, runtime):
        self.entries.update({identifier: runtime})

    def resolve(self, identifier):
        candidates = dict.fromkeys((identifier, identifier.partition(":")[0]))
        for candidate in candidates:
            if candidate in self.entries:
                return self.entries[candidate]
        return None

    def names(self):
        return [*self.entries]


_catalog = _RuntimeCatalog()
_providers = _catalog.entries


def register_agent_provider(provider_id: str, cls: type[AgentProvider]) -> None:
    _catalog.register(provider_id, cls)


def get_agent_provider_class(provider_id: str) -> type[AgentProvider] | None:
    return _catalog.resolve(provider_id)


def list_agent_providers() -> list[str]:
    return _catalog.names()
