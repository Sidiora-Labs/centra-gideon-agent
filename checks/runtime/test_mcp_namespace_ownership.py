"""Canonical MCP names cannot be claimed by application tool providers."""

from gideon.integrations.mcp_delegated import McpDelegatedToolProvider
from gideon.integrations.tool_providers.registry import (
    _namespace_refusal,
    _registration_for,
)
from gideon.interfaces.dashboard.handlers.mcp import _is_valid_mcp_name


def test_mcp_tool_namespace_is_reserved_for_trusted_mcp_owners() -> None:
    canonical = ["mcp/github/search_repositories"]
    assert _namespace_refusal("app", canonical) == "mcp_namespace_reserved"
    assert _namespace_refusal("core", canonical) == "mcp_namespace_reserved"
    assert _namespace_refusal("mcp", canonical) == ""
    assert _namespace_refusal("mcp", ["mcp_resources_list"]) == ""


def test_genuine_delegated_mcp_provider_keeps_its_stable_instance_identity() -> None:
    provider = McpDelegatedToolProvider("session-7")
    registration = _registration_for(provider)
    assert registration.owner_type == "mcp"
    assert registration.owner == provider.name
    assert registration.instance_id == "session-7"
    assert _namespace_refusal(registration.owner_type, ["mcp_resources_list"]) == ""


def test_mcp_server_names_cannot_contain_namespace_separator() -> None:
    assert _is_valid_mcp_name("github-tools")
    assert not _is_valid_mcp_name("github/tools")
    assert not _is_valid_mcp_name("mcp/github")
