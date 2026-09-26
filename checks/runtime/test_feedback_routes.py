"""FEEDBACK-SIGNAL S1 — the /api/feedback route surface.

The shared error envelope, the kill-switch 404, and the app-namespace forcing
(an app-scoped token can never impersonate a core producer).
"""

from __future__ import annotations

import json

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition import feedback as fb
from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.feedback import register_feedback_routes
from gideon.interfaces.dashboard.state import ConsoleState, _ChatSession


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    import gideon.core.config.loader as cfg
    import gideon.extensions.providers.entity_routes as er

    monkeypatch.setattr(cfg, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        er,
        "_entity_settings_path",
        lambda entity: tmp_path / "entity_settings" / f"{entity}.json",
    )
    fb._invalidate()
    yield tmp_path
    fb._invalidate()


def _make_app(app_token_name: str = "", state: ConsoleState | None = None) -> web.Application:
    app = web.Application()
    if state is not None:
        app["state"] = state
    if app_token_name:

        @web.middleware
        async def stamp_app(request, handler):
            request["app"] = app_token_name
            return await handler(request)

        app.middlewares.append(stamp_app)
    register_feedback_routes(app)
    return app


def _chat_state(tmp_path, owner_id: str = "owner-a") -> ConsoleState:
    directory = ConversationDirectory(AppConfig.load())
    state = ConsoleState(
        sessions=directory,
        start_time=0.0,
        conversation_log=ConversationLog(base_dir=tmp_path),
        owner_id=owner_id,
    )
    session = _ChatSession("chat-feedback")
    session.messages = [
        {"role": "user", "content": "What happened?"},
        {"role": "assistant", "content": "Here is the answer."},
    ]
    state._sessions[session.key] = session
    return state


BODY = {
    "target_kind": "inbox_classification",
    "target_id": "item-1",
    "verdict": "down",
    "reason": "wrong",
    "producer_kind": "prompt",
    "producer_id": "native:inbox-classify",
}


class TestRecordRoute:
    @pytest.mark.asyncio
    async def test_record_and_hydrate(self):
        async with TestClient(TestServer(_make_app())) as c:
            resp = await c.post("/api/feedback", json=BODY)
            assert resp.status == 200
            got = await (
                await c.get("/api/feedback/target/inbox_classification/item-1")
            ).json()
            assert got["verdict"] == "down" and got["reason"] == "wrong"

    @pytest.mark.asyncio
    async def test_missing_target_hydrates_null(self):
        async with TestClient(TestServer(_make_app())) as c:
            got = await (
                await c.get("/api/feedback/target/inbox_classification/nope")
            ).json()
            assert got["verdict"] is None

    @pytest.mark.asyncio
    async def test_unknown_target_kind_is_not_read(self):
        async with TestClient(TestServer(_make_app())) as c:
            response = await c.get("/api/feedback/target/unknown/item-1")
            assert response.status == 404

    @pytest.mark.asyncio
    async def test_bad_bodies_rejected(self):
        async with TestClient(TestServer(_make_app())) as c:
            assert (
                await c.post("/api/feedback", json={**BODY, "verdict": "meh"})
            ).status == 400
            assert (
                await c.post("/api/feedback", json={**BODY, "target_kind": "chat"})
            ).status == 400
            assert (
                await c.post("/api/feedback", json={**BODY, "target_id": ""})
            ).status == 400
            assert (await c.post("/api/feedback", data="not json")).status == 400

    @pytest.mark.asyncio
    async def test_app_caller_forcibly_namespaced(self):
        """An app-scoped token's producer is forced to app:<name>:<producer> —
        it can never impersonate a core producer (e.g. a bound prompt)."""
        async with TestClient(TestServer(_make_app(app_token_name="weather"))) as c:
            resp = await c.post("/api/feedback", json=BODY)
            assert resp.status == 200
        rec = fb.current_verdict("inbox_classification", "item-1")
        assert rec is not None
        assert rec.producer_kind == "app"
        assert rec.producer_id == "weather:native:inbox-classify"
        assert rec.source_app == "weather"

    @pytest.mark.asyncio
    async def test_kill_switch_404s_every_route(self, isolated):
        (isolated / "config.json").write_text(
            json.dumps({"feedback": {"enabled": False}})
        )
        async with TestClient(TestServer(_make_app())) as c:
            assert (await c.post("/api/feedback", json=BODY)).status == 404
            assert (
                await c.get("/api/feedback/target/inbox_classification/x")
            ).status == 404
            assert (await c.get("/api/feedback/producers")).status == 404


class TestChatMessageFeedback:
    @pytest.mark.asyncio
    async def test_assistant_turn_round_trip_and_generic_route_is_closed(self, isolated):
        state = _chat_state(isolated)
        async with TestClient(TestServer(_make_app(state=state))) as client:
            path = "/api/chat/sessions/chat-feedback/feedback/1"
            missing = await (await client.get(path)).json()
            assert missing["verdict"] is None
            saved = await client.post(path, json={"verdict": "down", "reason": "Wrong fact"})
            assert saved.status == 200
            assert (await saved.json())["verdict"] == "down"
            hydrated = await (await client.get(path)).json()
            assert hydrated == {"verdict": "down", "reason": "Wrong fact"}
            assert fb.current_verdict("chat_message", "owner-a:chat-feedback:1").session_key == "chat-feedback"
            bypass = await client.post("/api/feedback", json={
                "target_kind": "chat_message", "target_id": "owner-a:chat-feedback:1", "verdict": "up",
            })
            assert bypass.status == 400
            assert (await (await client.get(path)).json())["verdict"] == "down"

    @pytest.mark.asyncio
    async def test_only_existing_assistant_turn_in_current_owner_session(self, isolated):
        owner = _chat_state(isolated)
        other_owner = _chat_state(isolated, owner_id="owner-b")
        other_owner._sessions.clear()
        async with TestClient(TestServer(_make_app(state=owner))) as client:
            for index in (0, 2, -1):
                response = await client.post(
                    f"/api/chat/sessions/chat-feedback/feedback/{index}", json={"verdict": "up"},
                )
                assert response.status == 404
            invalid = await client.post(
                "/api/chat/sessions/chat-feedback/feedback/1", json={"verdict": "maybe"},
            )
            assert invalid.status == 400
            recorded = await client.post(
                "/api/chat/sessions/chat-feedback/feedback/1", json={"verdict": "up"},
            )
            assert recorded.status == 200
        async with TestClient(TestServer(_make_app(state=other_owner))) as client:
            assert (await client.get("/api/chat/sessions/chat-feedback/feedback/1")).status == 404

    @pytest.mark.asyncio
    async def test_disk_only_session_rehydrates_before_feedback(self, isolated):
        state = _chat_state(isolated)
        state._sessions.clear()
        state.conversation_log.append("chat-feedback", "user", "From disk")
        state.conversation_log.append("chat-feedback", "assistant", "Stored answer")
        async with TestClient(TestServer(_make_app(state=state))) as client:
            path = "/api/chat/sessions/chat-feedback/feedback/1"
            saved = await client.post(path, json={"verdict": "down", "reason": "Outdated"})
            assert saved.status == 200
            assert state._sessions["chat-feedback"].messages[1]["role"] == "assistant"
            assert (await (await client.get(path)).json())["reason"] == "Outdated"
            assert (await client.get("/api/chat/sessions/unknown/feedback/1")).status == 404

    @pytest.mark.asyncio
    async def test_paged_history_preserves_visible_indices(self, isolated):
        state = _chat_state(isolated)
        log = state.conversation_log
        for role, content in (
            ("user", "First question"), ("assistant", "First answer"),
            ("user", "Second question"), ("assistant", "Second answer"),
        ):
            log.append("chat-feedback", role, content)
        live = state._sessions["chat-feedback"]
        live.messages = [
            {"role": "user", "content": "Second question"},
            {"role": "assistant", "content": "Second answer"},
        ]
        live._disk_older_count = 2
        async with TestClient(TestServer(_make_app(state=state))) as client:
            first = "/api/chat/sessions/chat-feedback/feedback/1"
            second = "/api/chat/sessions/chat-feedback/feedback/3"
            assert (await client.post(first, json={"verdict": "down"})).status == 200
            assert (await client.post(second, json={"verdict": "up"})).status == 200
            assert (await (await client.get(first)).json())["verdict"] == "down"
            assert (await (await client.get(second)).json())["verdict"] == "up"
            assert (await client.post("/api/chat/sessions/chat-feedback/feedback/2", json={"verdict": "up"})).status == 404

    @pytest.mark.asyncio
    async def test_kill_switch_covers_chat_feedback(self, isolated):
        (isolated / "config.json").write_text(json.dumps({"feedback": {"enabled": False}}))
        state = _chat_state(isolated)
        async with TestClient(TestServer(_make_app(state=state))) as client:
            path = "/api/chat/sessions/chat-feedback/feedback/1"
            assert (await client.post(path, json={"verdict": "up"})).status == 404
            assert (await client.get(path)).status == 404
            assert fb.current_verdict("chat_message", "owner-a:chat-feedback:1") is None


class TestProducersRoute:
    @pytest.mark.asyncio
    async def test_min_n_gating(self):
        for i in range(2):
            fb.record_feedback(
                target_kind="inbox_classification",
                target_id=f"i{i}",
                verdict="up",
                producer_kind="prompt",
                producer_id="native:inbox-classify",
            )
        async with TestClient(TestServer(_make_app())) as c:
            got = await (await c.get("/api/feedback/producers")).json()
        row = got["producers"][0]
        assert row["collecting"] is True and "accuracy" not in row

    @pytest.mark.asyncio
    async def test_accuracy_and_the_below_threshold_state(self):
        """``workflow_surfacing`` has no surfacing gate, so falling below the retire
        threshold proposes retirement and withholds NOTHING.

        This test used to assert ``suppressed is True`` here, which was the untrue claim
        `ENFORCED_SUPPRESSION_KINDS` exists to correct: only ``skill_synthesis`` can act on
        membership, and the Settings panel renders ``suppressed`` as "Stopped surfacing".
        The per-kind branches are covered in test_feedback_suppression_enforcement.py; what
        this route-level test owns is that ``accuracy`` is reported once ``min_n`` is met.
        """
        for i in range(5):
            fb.record_feedback(
                target_kind="proposal_content",
                target_id=f"d{i}",
                verdict="down",
                producer_kind="workflow_surfacing",
                producer_id="wf_x",
            )
        async with TestClient(TestServer(_make_app())) as c:
            got = await (await c.get("/api/feedback/producers")).json()
        row = next(r for r in got["producers"] if r["producer_id"] == "wf_x")
        assert row["accuracy"] == 0.0
        assert (
            row.get("proposal_only") is True
        ), "below threshold must report the honest state"
        assert (
            "suppressed" not in row
        ), "an unenforced kind must not claim it stopped surfacing"

    @pytest.mark.asyncio
    async def test_snooze_and_clear_round_trip(self):
        for i in range(5):
            fb.record_feedback(
                target_kind="proposal_content",
                target_id=f"d{i}",
                verdict="down",
                producer_kind="workflow_surfacing",
                producer_id="wf_x",
            )
        async with TestClient(TestServer(_make_app())) as c:
            body = {"producer_kind": "workflow_surfacing", "producer_id": "wf_x"}
            assert (
                await c.post("/api/feedback/producers/snooze", json=body)
            ).status == 200
            assert ("workflow_surfacing", "wf_x") not in fb.suppressed_producers()
            assert (
                await c.post("/api/feedback/producers/clear", json=body)
            ).status == 200
            assert (
                await c.post("/api/feedback/producers/snooze", json={})
            ).status == 400
