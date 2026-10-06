"""Actual native/ACP consumption receipts preserve canonical dashboard ingress."""

import asyncio
import json
import sys

import pytest
from aiohttp.test_utils import TestClient, TestServer
from test_dashboard_ingress_identity import application, state_at

from gideon.core.config import AppConfig
from gideon.engine.agents.native.runtime import NativeAgentRuntime
from gideon.engine.agents.provider import AgentRuntimeDefinition
from gideon.engine.steering import SteeringText
from gideon.integrations.acp.dialect import ACPDialect
from gideon.integrations.acp.session import AcpConnection
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.interfaces.dashboard.chat_fork import (
    api_chat_session_fork,
    api_chat_session_fork_rewound,
)
from gideon.interfaces.dashboard.chat_handlers import _maybe_cancel_and_replace
from gideon.interfaces.dashboard.chat_persistence import (
    _rehydrate_session_from_history,
    save_session_to_history,
)
from gideon.interfaces.dashboard.chat_regenerate import (
    api_chat_session_edit_resend,
    api_chat_session_regenerate,
)
from gideon.interfaces.dashboard.chat_runner import commit_consumed_steering
from gideon.interfaces.dashboard.token_auth import generate_token
from gideon.security.approval_answer import OWNER, Principal, ingress_record


def accepted(state, session, text="added request"):
    record = ingress_record(Principal(OWNER, "sir"), "dashboard:" + session.key, text)
    session._initiator = record["principal"]
    session._pending_steers[record["source_event_id"]] = {
        "id": record["source_event_id"],
        "content": text,
        "meta": {"ingress": record},
    }
    carrier = SteeringText(text, meta={"ingress": record})
    carrier.on_consumed = lambda value: commit_consumed_steering(
        state, session, value, "current-turn"
    )
    save_session_to_history(state, session, force=True)
    return carrier, record


def test_native_actual_history_insertion_commits_once_and_restart_never_replays(
    tmp_path,
):
    state = state_at(tmp_path)
    session = state.get_or_create_session("native")
    text, record = accepted(state, session)
    pending_restore = _rehydrate_session_from_history(state_at(tmp_path), session.key)
    assert pending_restore._queue[0]["meta"]["ingress"] == record
    provider = OpenAIProvider(
        model="local-test",
        credential=Credential("local", "api_key", "local-only"),
        base_url="http://127.0.0.1:1/v1",
    )
    runtime = NativeAgentRuntime(
        definition=AgentRuntimeDefinition(name="native", model="local-test"),
        model_provider=provider,
        cwd=tmp_path,
    )
    runtime.set_steer_source([text].copy)
    assert runtime._drain_steers_into_history()
    assert runtime._messages[-1]["content"].endswith(str(text))
    assert session.messages[-1]["meta"]["ingress"] == record
    assert text.consumed and not session._pending_steers
    text.acknowledge()
    restored = _rehydrate_session_from_history(state_at(tmp_path), session.key)
    assert len(restored.messages) == 1 and not restored._queue
    assert restored.messages[0]["meta"]["turn_id"] == "current-turn"


@pytest.mark.asyncio
@pytest.mark.parametrize("reject", [False, True])
async def test_actual_acp_subprocess_receipt_only_commits_accepted_steering(
    tmp_path, reject
):
    state = state_at(tmp_path / "history")
    session = state.get_or_create_session("acp")
    text, record = accepted(state, session)
    # Wire peer deliberately controls success/error; production AcpProcess,
    # FrameRouter, AcpConnection and AcpSession perform all transport/receipt work.
    script = tmp_path / "wire_peer.py"
    script.write_text(
        'import sys,json\nfor line in sys.stdin:\n r=json.loads(line)\n if "id" not in r: continue\n result={"sessionId":"S1"} if r["method"]=="session/new" else {"stopReason":"end_turn"}\n reply={"jsonrpc":"2.0","id":r["id"]}\n if r["method"]=="session/prompt" and '
        + str(reject)
        + ': reply["error"]={"code":-32000,"message":"rejected"}\n else: reply["result"]=result\n print(json.dumps(reply),flush=True)\n'
    )
    dialect = ACPDialect()
    dialect.supports_mid_turn_prompt = True
    connection = await AcpConnection.spawn(
        command=[sys.executable, str(script)],
        work_dir=tmp_path,
        dialect=dialect,
        sandbox_mode="none",
    )
    try:
        acp = await connection.new_session({"cwd": str(tmp_path), "mcpServers": []})
        pending = [text]
        acp.set_steer_source(lambda: [pending.pop()] if pending else [])
        assert await acp._deliver_steers_at_tool_boundary() == 1
        for _ in range(100):
            if not acp._steer_inflight:
                break
            await asyncio.sleep(0.01)
        assert not acp._steer_inflight
        assert text.consumed is (not reject)
        assert len(session.messages) == (0 if reject else 1)
        if reject:
            assert acp.undelivered_steers()[0].meta["ingress"] == record
            assert session._pending_steers
        else:
            assert session.messages[0]["meta"]["ingress"] == record
    finally:
        await connection.close()


@pytest.mark.asyncio
async def test_actual_cancel_replace_queue_retains_and_persists_accepted_actor(
    tmp_path,
):
    cfg = AppConfig.load()
    cfg.resilience.mid_turn_policy = "cancel_and_replace"
    cfg.resilience.cancel_replace_min_interval_secs = 0
    cfg.save()
    state = state_at(tmp_path)
    session = state.get_or_create_session("cancel")
    record = ingress_record(Principal(OWNER, "sir"), "dashboard:cancel", "replacement")
    result = await _maybe_cancel_and_replace(
        state, session, "replacement", meta={"ingress": record}
    )
    assert result is not None and result.status == 200
    restored = _rehydrate_session_from_history(state_at(tmp_path), session.key)
    assert restored._queue[0]["meta"]["ingress"] == record


@pytest.mark.asyncio
async def test_actual_fork_and_rewound_fork_preserve_original_source_and_scope(
    tmp_path,
):
    state = state_at(tmp_path)
    session = state.get_or_create_session("fork", project_id="project-one")
    record = ingress_record(Principal(OWNER, "sir"), "dashboard:fork", "original")
    session._initiator = record["principal"]
    session.append("user", "original", meta={"ingress": record})
    session.append("assistant", "answer")
    session.messages[0]["rewound"] = [
        {"messages": [dict(m) for m in session.messages], "ts": "2026-10-06T00:00:00Z"}
    ]
    app = application(state)
    app.router.add_post("/fork/{session}", api_chat_session_fork)
    app.router.add_post("/rewound/{session}", api_chat_session_fork_rewound)
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": "Bearer " + generate_token("sir")}
        for route, body in [("/fork/fork", {}), ("/rewound/fork", {"index": 0})]:
            response = await client.post(route, headers=headers, json=body)
            assert response.status == 200, await response.text()
            key = (await response.json())["key"]
            fork = state.get_session(key)
            assert fork._initiator == session._initiator
            assert (
                fork.project_id == session.project_id
                and fork.memory_mode == session.memory_mode
            )
            assert fork.messages[0]["meta"]["ingress"] == record
            fork.messages[0]["meta"]["ingress"]["source_user"] = "changed"
            assert session.messages[0]["meta"]["ingress"]["source_user"] == "owner:sir"


@pytest.mark.asyncio
async def test_edit_rewind_and_regenerate_record_new_actor_without_rewriting_original_evidence(
    tmp_path,
):
    state = state_at(tmp_path)
    session = state.get_or_create_session("edit")
    record = ingress_record(
        Principal(OWNER, "original"), "dashboard:edit", "old request"
    )
    session._initiator = record["principal"]
    session.append("user", "old request", meta={"ingress": record})
    session.append("assistant", "old answer")
    app = application(state)
    app.router.add_post("/edit/{session}", api_chat_session_edit_resend)
    app.router.add_post("/regenerate/{session}", api_chat_session_regenerate)
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": "Bearer " + generate_token("sir")}
        response = await client.post(
            "/edit/edit",
            headers=headers,
            json={"index": 0, "content": "new request", "rewind": True},
        )
        assert response.status == 200, await response.text()
        current = session.messages[0]
        assert current["meta"]["ingress"]["source_user"] == "owner:sir"
        assert current["rewound"][0]["messages"][0]["meta"]["ingress"] == record
        if session.task:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
        # No provider completion is claimed; isolate the canonical rerun admission.
        session.append("assistant", "answer")
        response = await client.post("/regenerate/edit", headers=headers)
        assert response.status == 200, await response.text()
        assert session.messages[0]["meta"]["ingress"]["source_user"] == "owner:sir"
        assert (
            session.messages[0]["meta"]["replay_ingress"]["source_user"] == "owner:sir"
        )
        assert session._initiator == record["principal"]
        if session.task:
            session.task.cancel()
            await asyncio.gather(session.task, return_exceptions=True)
