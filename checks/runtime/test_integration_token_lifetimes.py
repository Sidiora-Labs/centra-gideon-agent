from __future__ import annotations

import hashlib
import json
import time

import pytest


def test_surface_tokens_are_capped_replaced_revoked_and_digest_only(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.integrations.inbound import tokens

    first = "surface-secret-never-persist-this"
    issued = tokens.issue_surface_token("mcp", first, now=1_000)
    assert issued["expires_at"] == 1_000 + 90 * 86_400
    raw = tokens.registry_path().read_text(encoding="utf-8")
    digest = hashlib.sha256(first.encode()).hexdigest()
    assert digest in raw
    assert first not in raw
    assert tokens.surface_usable("mcp", first, now=1_001)
    with pytest.raises(ValueError, match="90d"):
        tokens.issue_surface_token("mcp", "too-long-token", "91d", now=1_100)

    second = "second-surface-secret"
    tokens.issue_surface_token("mcp", second, now=2_000)
    assert tokens.surface_token("mcp", first, now=2_001)["state"] == tokens.REPLACED
    assert not tokens.surface_usable("mcp", first, now=2_001)
    row = next(
        row
        for row in tokens.surface_rows(now=2_002)
        if row["surface"] == "mcp" and row["issued_at"] == 2_000
    )
    assert tokens.revoke_surface_id("mcp", int(row["issued_at"] * 1_000_000), now=2_003)
    assert not tokens.surface_usable("mcp", second, now=2_004)
    assert "revoked" in (tokens.refusal("mcp", second) or "")


def test_real_client_ledger_expires_and_revokes_across_lookup(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.integrations.inbound import clients, tokens

    client, bearer = clients.create_client(
        "Calendar bridge", surfaces=["mcp"], ttl="1m"
    )
    assert clients.lookup_by_token(bearer, "mcp")[0] == client
    registry = json.loads(tokens.registry_path().read_text(encoding="utf-8"))
    token_row = registry["tokens"][client.token_hash]
    token_row["expires_at"] = time.time() - 1
    tokens.registry_path().write_text(json.dumps(registry), encoding="utf-8")
    expired, reason = clients.lookup_by_token(bearer, "mcp")
    assert expired is None
    assert "expired" in reason

    client2, bearer2 = clients.create_client("MCP probe", surfaces=["mcp"])
    assert clients.revoke_client(client2.client_id)
    assert clients.lookup_by_token(bearer2, "mcp")[0] is None
    assert "revoked" in (tokens.refusal("mcp", bearer2) or "")


def test_unreadable_registry_refuses_surface_and_client_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.integrations.inbound import tokens

    token = "known-surface-secret"
    tokens.issue_surface_token("mcp", token)
    tokens.registry_path().write_text("{", encoding="utf-8")
    assert not tokens.surface_usable("mcp", token)
