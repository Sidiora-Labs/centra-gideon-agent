"""Gideon agents package — marketplace abstraction and local implementation."""

from gideon.agents.marketplace import (
    AgentDefinition,
    AgentMarketplace,
    AgentMarketplaceRegistry,
    LocalAgentMarketplace,
    get_default_agent_registry,
)

__all__ = [
    "AgentDefinition",
    "AgentMarketplace",
    "AgentMarketplaceRegistry",
    "LocalAgentMarketplace",
    "get_default_agent_registry",
]
