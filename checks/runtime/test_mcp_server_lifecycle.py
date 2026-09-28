"""MCP secret references remain owner-bound through update and cleanup."""

from __future__ import annotations

import pytest


def test_server_credentials_are_owner_bound_and_purge_isolated(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))

    from gideon.core.config.secret_refs import ForeignSecretReference, SecretOwner, purge
    from gideon.extensions.providers.mcp_instances import resolve_server_credentials
    from gideon.integrations.mcp_secret_refs import store_server_credentials

    alpha = store_server_credentials("alpha", {
        "url": "https://alpha.example.test/mcp",
        "transport": "streamable_http",
        "headers": {"Authorization": "Bearer alpha-secret"},
        "env": {"SERVICE_TOKEN": "alpha-env-secret"},
    })
    beta = store_server_credentials("beta", {
        "url": "https://beta.example.test/mcp",
        "transport": "sse",
        "headers": {"Authorization": "Bearer beta-secret"},
        "env": {"SERVICE_TOKEN": "beta-env-secret"},
    })

    assert alpha["headers"]["Authorization"].startswith("{{secret:GIDEON_SECRET_MCP_ALPHA_")
    assert beta["headers"]["Authorization"].startswith("{{secret:GIDEON_SECRET_MCP_BETA_")
    assert resolve_server_credentials("alpha", alpha)["headers"]["Authorization"] == "Bearer alpha-secret"
    with pytest.raises(ForeignSecretReference):
        resolve_server_credentials("beta", {"headers": alpha["headers"]})

    purge([SecretOwner("MCP", "alpha").prefix])
    assert resolve_server_credentials("beta", beta)["headers"]["Authorization"] == "Bearer beta-secret"
    with pytest.raises(ValueError):
        resolve_server_credentials("alpha", alpha)
