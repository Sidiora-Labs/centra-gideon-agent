"""Tests for MCP connection pooling, session isolation, idle eviction, and the
spawn circuit-breaker (rel-mcp-server-pooling #46).

These exercise the registry's routing/lifecycle logic directly — no real MCP
subprocess is spawned (connections are created but never `ensure_started`), so
they run without the optional ``mcp`` SDK extra.
"""

from __future__ import annotations

import pytest

from gideon.integrations.mcp_client import (
    _BREAKER_THRESHOLD,
    McpClientRegistry,
    McpServerConn,
    _conn_key,
    _is_poolable,
    with_mcp_session_eviction,
)

_POOLABLE = {"command": "x", "poolable": True}
_STATEFUL = {"command": "y"}


def test_poolable_flag():
    assert _is_poolable({"command": "x", "poolable": True}) is True
    assert _is_poolable({"command": "x"}) is False
    assert _is_poolable({"command": "x", "poolable": False}) is False


def test_conn_key_poolable_shares_scope():
    k1 = _conn_key("s", _POOLABLE, "sess-1")
    k2 = _conn_key("s", _POOLABLE, "sess-2")
    assert k1 == k2
    assert k1[0] == "s" and k1[1] == ""
    assert len(k1) == 3 and k1[2]


def test_conn_key_stateful_scopes_by_session():
    assert _conn_key("s", _STATEFUL, "sess-1")[:2] == ("s", "sess-1")
    assert _conn_key("s", _STATEFUL, "sess-2")[:2] == ("s", "sess-2")
    assert _conn_key("s", _STATEFUL, "")[:2] == ("s", "")


def test_conn_key_same_name_different_spec_disambiguates():
    a = _conn_key("dup", {"command": "server-a", "poolable": True}, "")
    b = _conn_key("dup", {"command": "server-b", "poolable": True}, "")
    assert a != b and a[0] == b[0] == "dup" and a[1] == b[1] == ""
    c = _conn_key("dup", {"command": "server-a", "args": ["--x"], "poolable": True}, "")
    assert c != a


def test_poolable_server_shared_across_sessions():
    reg = McpClientRegistry()
    reg.load_from_specs({"shared": _POOLABLE})
    c1 = reg.get("shared", "sess-1")
    c2 = reg.get("shared", "sess-2")
    assert c1 is c2
    assert c1.scope == ""


def test_stateful_server_isolated_per_session():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL})
    c1 = reg.get("browser", "sess-1")
    c2 = reg.get("browser", "sess-2")
    assert c1 is not c2
    assert c1.scope == "sess-1"
    assert c2.scope == "sess-2"
    assert reg.get("browser", "sess-1") is c1


def test_unknown_server_returns_none():
    reg = McpClientRegistry()
    reg.load_from_specs({"known": _POOLABLE})
    assert reg.get("nope", "sess-1") is None


def test_canonical_connection_created_eagerly():
    reg = McpClientRegistry()
    reg.load_from_specs({"a": _STATEFUL})
    names = {n for n, _ in reg.items()}
    assert names == {"a"}


def test_items_lists_each_server_once_despite_per_session_conns():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL})
    reg.get("browser", "sess-1")
    reg.get("browser", "sess-2")
    names = [n for n, _ in reg.items()]
    assert names == ["browser"]


def test_removed_server_drops_all_scoped_conns():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL, "keep": _POOLABLE})
    reg.get("browser", "sess-1")
    reg.get("browser", "sess-2")
    assert len(reg._conns) == 4
    reg.load_from_specs({"keep": _POOLABLE})
    remaining = {k[0] for k in reg._conns}
    assert remaining == {"keep"}


def test_disabled_server_not_registered():
    reg = McpClientRegistry()
    reg.load_from_specs({"off": {"command": "x", "disabled": True}})
    assert reg.get("off", "s") is None
    assert reg.items() == []


def test_evict_session_drops_only_that_sessions_conns():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL, "shared": _POOLABLE})
    reg.get("browser", "sess-1")
    reg.get("browser", "sess-2")
    reg.get("shared", "sess-1")
    reg.evict_session("sess-1")
    prefixes = {(k[0], k[1]) for k in reg._conns}
    assert ("browser", "sess-1") not in prefixes
    assert ("browser", "sess-2") in prefixes
    assert ("shared", "") in prefixes


def test_evict_empty_session_is_noop():
    reg = McpClientRegistry()
    reg.load_from_specs({"browser": _STATEFUL})
    before = set(reg._conns)
    reg.evict_session("")
    assert set(reg._conns) == before


def test_sweep_idle_reaps_stale_only():
    reg = McpClientRegistry()
    reg.load_from_specs({"a": _STATEFUL})
    conn = reg.get("a", "sess-1")
    conn._last_used -= 10_000
    reaped = reg.sweep_idle(ttl_secs=600)
    assert reaped == 1
    prefixes = {(k[0], k[1]) for k in reg._conns}
    assert ("a", "sess-1") not in prefixes
    assert ("a", "") in prefixes


def test_sweep_idle_keeps_fresh():
    reg = McpClientRegistry()
    reg.load_from_specs({"a": _POOLABLE})
    reg.get("a", "sess-1")
    assert reg.sweep_idle(ttl_secs=600) == 0


def test_breaker_trips_after_threshold_failures():
    conn = McpServerConn("bad", _STATEFUL)
    for _ in range(_BREAKER_THRESHOLD):
        conn._note_failure()
    import time

    assert conn._breaker_until > time.monotonic()


@pytest.mark.asyncio
async def test_breaker_blocks_respawn_during_cooldown():
    conn = McpServerConn("bad", _STATEFUL)
    import time

    conn._consecutive_failures = _BREAKER_THRESHOLD
    conn._breaker_until = time.monotonic() + 60
    ok = await conn.ensure_started()
    assert ok is False
    assert conn._task is None
    assert "circuit breaker" in conn.error


def test_touch_updates_last_used():
    conn = McpServerConn("a", _POOLABLE)
    conn._last_used = 0.0
    conn.touch()
    assert conn._last_used > 0.0


@pytest.mark.asyncio
async def test_with_mcp_session_eviction_runs_prior_then_evicts():
    calls: list[str] = []

    async def prior(sk: str) -> None:
        calls.append(f"prior:{sk}")

    wrapped = with_mcp_session_eviction(prior)
    await wrapped("sess-9")
    assert calls == ["prior:sess-9"]


@pytest.mark.asyncio
async def test_with_mcp_session_eviction_prior_failure_isolated():
    async def boom(sk: str) -> None:
        raise RuntimeError("consolidation broke")

    wrapped = with_mcp_session_eviction(boom)
    await wrapped("sess-9")


@pytest.mark.asyncio
async def test_with_mcp_session_eviction_none_prior():
    wrapped = with_mcp_session_eviction(None)
    await wrapped("sess-9")


def test_reconcile_replaces_conn_when_spec_content_changes():
    reg = McpClientRegistry()
    reg.load_from_specs({"srv": {"command": "old", "poolable": True}})
    c_old = reg.get("srv", "")
    reg.load_from_specs({"srv": {"command": "new", "poolable": True}})
    c_new = reg.get("srv", "")
    assert c_new is not c_old
    canon = [k for k in reg._conns if k[0] == "srv" and k[1] == ""]
    assert len(canon) == 1


def test_pool_stats_counts_spawns_reuse_and_shape():
    reg = McpClientRegistry()
    reg.load_from_specs({"shared": _POOLABLE, "browser": _STATEFUL})
    reg.get("shared", "sess-1")
    reg.get("shared", "sess-2")
    reg.get("browser", "sess-1")
    s = reg.pool_stats()
    assert s["configured_servers"] == 2
    assert s["shared_conns"] >= 1 and s["session_conns"] >= 1
    assert s["spawns"] >= 3 and s["served"] >= 3
    assert s["reused"] == max(0, s["served"] - s["spawns"])
