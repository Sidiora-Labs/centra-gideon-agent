"""MCP memory tool effects and model-visible rule projection."""

import asyncio
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gideon.assurance.validation import ValidationError


@pytest.mark.asyncio
async def test_read_rule_needs_no_write_grant_and_mutation_still_requires_approval(
    tmp_path, monkeypatch
):
    import secrets

    from gideon.automation.workflows.batch_compile import is_write_tool
    from gideon.cognition.memory_service import resolve_lesson_scope
    from gideon.cognition.proactive.approval import ApprovalRule, Verdict, rule_to_value
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations import mcp_memory
    from gideon.interfaces.dashboard import token_auth
    from gideon.interfaces.dashboard.handlers import memory, schedule
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.security.approval_answer import agent, refusal

    home = tmp_path / "gideon-home"
    home.mkdir()
    secret = secrets.token_urlsafe(32)
    session_key = "mcp-memory-read-test"
    (home / ".local_secret").write_text(secret, encoding="utf-8")
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.setenv("GIDEON_SESSION_KEY", session_key)
    monkeypatch.delenv("GIDEON_DEV_NO_AUTH", raising=False)
    monkeypatch.delenv("GIDEON_BYPASS_LOCAL_NETWORKS", raising=False)

    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=time.time())
    state.get_or_create_session(session_key)
    store = memory._get_memory(state).vector_store
    rule = ApprovalRule(
        pattern="archive:sender:example.test",
        verdict=Verdict.DENY,
        scope="global",
        hit_count=2,
    )
    value = rule_to_value(rule)
    value.update(
        {
            "owner_credential": "owner-secret-marker",
            "operator_grant": "operator-secret-marker",
            "tenant_secret": "other-tenant-secret-marker",
        }
    )
    assert store.set_semantic(rule.key, value, 0.9, "user") is None

    tools = {tool["name"]: tool for tool in mcp_memory._list_tools()}
    assert tools["approval_rules_list"]["annotations"]["readOnlyHint"] is True
    assert tools["triage_rules"]["annotations"]["readOnlyHint"] is False
    assert not is_write_tool("approval_rules_list")
    assert is_write_tool("triage_rules")
    assert refusal(agent("mcp-session"))

    app = web.Application(
        middlewares=[
            token_auth.token_auth_middleware(
                mixed_internal_paths=frozenset({"/api/lessons"}),
                mixed_internal_routes=frozenset(
                    {("GET", "/api/memory/approval-rules")}
                ),
                internal_secret=secret,
            ),
        ]
    )
    app["state"] = state
    app.router.add_get("/api/memory/approval-rules", memory.api_memory_approval_rules)
    app.router.add_post(
        "/api/memory/approval-rules", memory.api_memory_approval_rule_add
    )
    app.router.add_delete(
        "/api/memory/approval-rules/{key:.+}", memory.api_memory_approval_rule_delete
    )
    app.router.add_post("/api/lessons", schedule.api_lessons_create)

    async with TestClient(TestServer(app)) as client:
        monkeypatch.setenv("GIDEON_PORT", str(client.server.port))

        listed = await asyncio.to_thread(
            mcp_memory._call_tool_inner, "approval_rules_list", {}
        )
        assert "archive:sender:example.test" in listed
        assert all(
            marker not in listed
            for marker in (
                "owner-secret-marker",
                "operator-secret-marker",
                "other-tenant-secret-marker",
            )
        )

        for method, path, kwargs in (
            ("get", "/api/memory/approval-rules", {"headers": {}}),
            (
                "get",
                "/api/memory/approval-rules",
                {"headers": {"X-Internal-Secret": "wrong-secret"}},
            ),
        ):
            response = await getattr(client, method)(path, **kwargs)
            assert response.status in {401, 403}

        denied_post = await client.post(
            "/api/memory/approval-rules",
            json={"pattern": "archive:sender:example.test", "verdict": "deny"},
            headers={"X-Internal-Secret": secret},
        )
        assert denied_post.status in {401, 403}
        denied_delete = await client.delete(
            f"/api/memory/approval-rules/{rule.key}",
            headers={"X-Internal-Secret": secret},
        )
        assert denied_delete.status in {401, 403}

        remembered = await asyncio.to_thread(
            mcp_memory._call_tool_inner,
            "memory_remember",
            {
                "rule": "Prefer concise responses",
                "category": "preference",
                "negative": "Do not repeat resolved questions",
                "scope": "global",
            },
        )
        assert remembered == "Saved lesson (global): Prefer concise responses"

    with pytest.raises(ValidationError):
        mcp_memory._validate_args(
            "memory_remember", {"rule": "x", "category": "invalid"}
        )
    with pytest.raises(ValidationError):
        mcp_memory._validate_args(
            "memory_remember", {"rule": "x", "category": "knowledge", "scope": "operator"}
        )
    with pytest.raises(ValueError):
        resolve_lesson_scope("operator", None)
