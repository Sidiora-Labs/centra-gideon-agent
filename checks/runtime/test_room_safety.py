import asyncio
import json
import time
from dataclasses import asdict

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from hypothesis import given
from hypothesis import strategies as st

from gideon.core.config.loader import AppConfig
from gideon.engine.rooms import RoomMember, RoomStore, session_key
from gideon.engine.rooms.safety import (
    ProfileRefusal,
    RoomApprover,
    member_spend_scope,
    narrow_profile,
)
from gideon.engine.rooms.turn import RoomTurns
from gideon.engine.session import ConversationDirectory
from gideon.integrations.llm.events import (
    EVENT_COMPLETE,
    EVENT_PERMISSION_REQUEST,
    AgentEvent,
)
from gideon.integrations.mcp_core import reset_current_agent_id, set_current_agent_id
from gideon.interfaces.dashboard.handlers.rooms import setup_room_routes
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.security.guardrails.budgets import (
    Budget,
    current_run_budget,
    current_run_key,
    get_meter,
)
from gideon.security.guardrails.policy import (
    INTERACTIVE,
    TOOL_READ,
    profile_for_session,
    tool_grant_denial,
)


@pytest.fixture
def room_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.delenv("GIDEON_AGENT_ID", raising=False)
    (tmp_path / "config.json").write_text('{"rooms":{"enabled":true}}')
    return tmp_path


def test_default_read_and_explicit_narrowing_use_real_profiles():
    base = INTERACTIVE.with_overrides(scan_mode="redact", budget=Budget(100, 2))
    default = narrow_profile(base, None)
    assert default.tool_grants == TOOL_READ and default.approval == "ask"
    narrowed = narrow_profile(
        base,
        {
            "tool_grants": "custom",
            "tool_allowlist": ["read_file"],
            "scan_mode": "block",
            "budget": {"max_tokens": 20, "max_dollars": 1},
        },
    )
    assert narrowed.budget == Budget(20, 1)
    assert narrowed.scan_mode == "block" and narrowed.tool_allowlist == ("read_file",)
    assert not tool_grant_denial(
        "read_file", narrowed.tool_grants, narrowed.tool_allowlist
    )
    assert tool_grant_denial(
        "write_file", narrowed.tool_grants, narrowed.tool_allowlist
    )
    assert asdict(base)["tool_grants"] == "read_write"


@given(
    limit=st.integers(min_value=1, max_value=100000),
    extra=st.integers(min_value=1, max_value=10000),
)
def test_budget_narrowing_never_increases_or_removes_cap(limit, extra):
    base = INTERACTIVE.with_overrides(budget=Budget(max_tokens=limit))
    assert (
        narrow_profile(base, {"budget": {"max_tokens": limit}}).budget.max_tokens
        == limit
    )
    for candidate in (0, limit + extra):
        with pytest.raises(ProfileRefusal, match="widens"):
            narrow_profile(base, {"budget": {"max_tokens": candidate}})


@pytest.mark.parametrize("axis", ["egress_tier", "denylist_extra", "path_allowlist"])
def test_named_axes_refused_even_when_nominally_stricter(axis):
    with pytest.raises(ProfileRefusal, match=axis):
        narrow_profile(INTERACTIVE, {axis: [] if axis != "egress_tier" else "off"})


@pytest.mark.parametrize(
    "declaration",
    [
        {"approval": "auto"},
        {"approval": "hook_based"},
        {"scan_mode": "warn"},
        {"tool_grants": "read_write"},
        {"tool_grants": "custom", "tool_allowlist": ["*"]},
        {"tool_grants": "custom", "tool_allowlist": ["write_file"]},
        {"budget": {"max_dollars": float("nan")}},
        {"budget": {"max_tokens": -1}},
        {"budget": {"max_tokens": True}},
        {"unknown_axis": True},
        [],
    ],
)
def test_widening_and_malformed_declarations_fail_closed(declaration):
    base = INTERACTIVE.with_overrides(tool_grants="read", scan_mode="redact")
    with pytest.raises(ProfileRefusal):
        narrow_profile(base, declaration)


def test_custom_inheritance_cannot_gain_new_patterns():
    base = INTERACTIVE.with_overrides(
        tool_grants="custom", tool_allowlist=("read_file", "list_dir")
    )
    assert narrow_profile(base, None).tool_allowlist == base.tool_allowlist
    assert narrow_profile(base, {"tool_allowlist": ["read_file"]}).tool_allowlist == (
        "read_file",
    )
    for declaration in (
        {"tool_allowlist": ["read_*"]},
        {"tool_grants": "read"},
        {"tool_grants": "read_write"},
    ):
        with pytest.raises(ProfileRefusal):
            narrow_profile(base, declaration)


@pytest.mark.asyncio
async def test_profile_refusals_are_transcribed_and_posture_is_returned(room_home):
    store = RoomStore(room_home)
    room = store.create(
        "Safety",
        [
            {
                "id": "a",
                "agent": "default",
                "profile_narrowing": {"egress_tier": "off"},
            },
            {"id": "b", "agent": "default", "listen_policy": "none"},
        ],
    )
    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    messages = await RoomTurns(store, state).run(room.id, "Work")
    assert messages[-1]["role"] == "system"
    assert "Profile refused for a" in messages[-1]["content"]
    assert "egress_tier" in messages[-1]["content"]
    assert state.sessions.count == 0
    assert profile_for_session(session_key(room.id, "b")).tool_grants == "read"
    app = web.Application()
    setup_room_routes(app, store)
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/api/rooms/{room.id}")
        assert response.status == 200
        body = (await response.json())["room"]
        assert body["member_postures"]["a"]["allowed"] is False
        assert "egress_tier" in body["member_postures"]["a"]["refusal"]
        assert body["member_postures"]["b"]["tool_grants"] == "read"
        assert body["member_postures"]["b"]["approval"] == "ask"
        assert body["members"][0]["profile_narrowing"] == {"egress_tier": "off"}


@pytest.mark.asyncio
async def test_human_only_approval_uses_real_console_future(room_home):
    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    for identity in ("agent", "room:a:b", RoomMember("a", "default")):
        with pytest.raises(PermissionError):
            RoomApprover(state, identity=identity)
    token = set_current_agent_id("room-member")
    try:
        with pytest.raises(PermissionError):
            RoomApprover(state, identity="human")
    finally:
        reset_current_agent_id(token)
    approver = RoomApprover(state, identity="human")
    profile = narrow_profile(INTERACTIVE, None)
    denied = AgentEvent(EVENT_PERMISSION_REQUEST, title="write_file")
    assert await approver.approve("room:a:b", denied, profile) is False
    assert not state._approval_futures
    pending = asyncio.create_task(
        approver.approve(
            "room:a:b", AgentEvent(EVENT_PERMISSION_REQUEST, title="read_file"), profile
        )
    )
    await asyncio.sleep(0)
    approval_id = next(iter(state._approval_futures))
    assert not pending.done()
    assert state.resolve_approval(approval_id, True)
    assert await pending is True


def test_member_spend_scopes_are_isolated_reset_and_not_double_charged(room_home):
    profile = narrow_profile(INTERACTIVE, {"budget": {"max_tokens": 100}})
    meter = get_meter()
    with member_spend_scope("room:r:a", profile) as spend:
        assert current_run_key() == "room:r:a"
        assert current_run_budget() == Budget(max_tokens=100)
        meter.charge(10, 0.1, run_key=current_run_key())
        spend.record(
            AgentEvent(EVENT_COMPLETE, input_tokens=8, output_tokens=2, cost_usd=0.1)
        )
        with member_spend_scope("room:r:b", profile) as second:
            second.record(
                AgentEvent(
                    EVENT_COMPLETE, input_tokens=3, output_tokens=4, cost_usd=0.2
                )
            )
        assert current_run_key() == "room:r:a"
    assert current_run_key() == ""
    assert meter.run_totals("room:r:a").tokens == 10
    assert meter.run_totals("room:r:b").tokens == 7
    assert meter.day_totals().tokens == 17
    assert meter.day_totals().dollars == pytest.approx(0.3)
    with pytest.raises(RuntimeError):
        with member_spend_scope("room:r:a", profile):
            raise RuntimeError("abort")
    assert current_run_key() == ""
    meter.charge(100, 0, run_key="room:r:a")
    with pytest.raises(ProfileRefusal, match="budget exceeded"):
        with member_spend_scope("room:r:a", profile):
            pytest.fail("exhausted member entered spend scope")


@pytest.mark.asyncio
async def test_native_room_tool_invocation_enforces_read_tier(room_home):
    from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider
    from gideon.engine.agents.native.runtime import NativeAgentRuntime
    from gideon.engine.agents.provider import AgentRuntimeDefinition
    from gideon.integrations.llm.credentials import Credential
    from gideon.integrations.llm.openai import OpenAIProvider

    store = RoomStore(room_home)
    room = store.create("Read only", [{"id": "a", "agent": "default"}])
    key = session_key(room.id, "a")
    model = OpenAIProvider(
        model="gpt-4.1",
        credential=Credential("local-test", "api_key", "unused-no-network"),
    )
    provider = NativeBuiltinToolProvider(room_home, session_key=key)
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition("default"),
        model_provider=model,
        tool_providers=[provider],
        cwd=room_home,
        session_key=key,
    )
    runtime._tool_index = {"write_file": provider, "read_file": provider}
    target = room_home / "not-written.txt"
    metadata = {}
    try:
        result = await runtime._invoke(
            "write_file", {"path": str(target), "content": "denied"}, meta_sink=metadata
        )
        assert "read-only" in result
        assert metadata["ok"] is False
        assert not target.exists()
    finally:
        await model.shutdown()


def test_implicit_tool_tier_stays_read_only_under_custom_parent():
    base = INTERACTIVE.with_overrides(
        tool_grants="custom", tool_allowlist=("read_file", "write_file", "*")
    )
    for declaration in (None, {}, {"approval": "ask"}, {"scan_mode": "block"}):
        profile = narrow_profile(base, declaration)
        assert profile.tool_allowlist == ("read_file",)
        assert tool_grant_denial(
            "write_file", profile.tool_grants, profile.tool_allowlist
        )


def test_agent_process_cannot_claim_human_approver(room_home, monkeypatch):
    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    monkeypatch.setenv("GIDEON_SESSION_KEY", "room:r:a")
    with pytest.raises(PermissionError):
        RoomApprover(state, identity="human")
