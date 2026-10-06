from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from openai import APIStatusError, AsyncOpenAI

from gideon.cognition.context import PromptAssembler
from gideon.cognition.history import ConversationLog
from gideon.cognition.memory import MemoryJournal
from gideon.core.config.external_access import ExternalAccessConfig
from gideon.core.config.external_access import ExternalAccessSurfaceConfig as Surface
from gideon.core.config.loader import AgentProfile, AppConfig
from gideon.core.config.transactions import mutate_config
from gideon.engine.session import ConversationDirectory
from gideon.extensions.providers.provider_bridge import create_provider_factory
from gideon.extensions.providers.use_cases import save_active_models
from gideon.extensions.skills.loader import ProcedureLibrary
from gideon.integrations.inbound import auth, caps, clients
from gideon.integrations.inbound import openai_dialect as dialect
from gideon.integrations.llm.base import EVENT_COMPLETE, EVENT_TEXT_CHUNK, LLMEvent
from gideon.integrations.llm.capabilities import Capability, ProviderCapability
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.integrations.llm.registry import ProviderEntry, ProviderRegistry
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.chat_handlers import (
    _run_chat_scoped,
    api_chat_sessions,
)
from gideon.interfaces.dashboard.chat_persistence import (
    resolve_session,
    save_session_to_history,
)
from gideon.interfaces.dashboard.handlers import external_access as ea
from gideon.interfaces.dashboard.state import ConsoleState

_SURFACES = ("OPENAI", "MCP", "A2A", "CAPTURE", "BRIDGE")

ENTRY = "recorder"
AGENT = "researcher"
TAG = "kai"
CLIENTS = "/api/external-access/clients"

#: What one request says, and what only that request says.
FIRST = "[q1] The gate code for the north entrance is 4417. Note it."
SECOND = "[q2] What is the gate code?"
FIRST_SECRET = "4417"


def _marker(text: str) -> str:
    """The request marker (``[q1]``) a message ends on, as the model reads it."""
    found = re.findall(r"\[q[A-Za-z0-9]+\]", text)
    return found[-1] if found else ""


def _asked_for(call: list[dict]) -> str:
    """The marker of the request a model call answers: the one its last user message ends on."""
    last = next((m for m in reversed(call) if m.get("role") == "user"), {})
    return _marker(str(last.get("content") or ""))


class _World:
    """A gateway with one agent, the External Access routes, and the OpenAI-compatible endpoint."""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        #: Every message list the model was handed, in order, and the switch that holds a turn.
        self.asked: list[list[dict]] = []
        self.was_asked = asyncio.Event()
        self.hold = asyncio.Event()
        self.hold.set()
        registry = ProviderRegistry()
        registry.register_type(
            ProviderCapability(
                type=ENTRY,
                capabilities=frozenset({Capability.CHAT}),
                supports_streaming=True,
                supports_tools=False,
                supports_embeddings=False,
                supports_vision=False,
                max_context_tokens=32768,
            ),
            lambda *, entry, session_key=None, **kw: OpenAIProvider(
                model=entry.model,
                credential=Credential(
                    "local-test", "api_key", "controlled-local-token"
                ),
                base_url=self.model_url,
            ),
        )
        registry.register_entry(
            ProviderEntry(name=ENTRY, type=ENTRY, model="research-1")
        )
        monkeypatch.setattr(
            "gideon.integrations.llm.registry.get_default_registry", lambda: registry
        )
        # Short deadlines, so a reader left waiting on a turn it is never handed fails in seconds.
        monkeypatch.setattr(dialect, "TURN_TIMEOUT_SECS", 8.0)
        monkeypatch.setattr(dialect, "_POLL_TIMEOUT_SECS", 0.5)

        cfg = AppConfig.load()
        cfg.agents[AGENT] = AgentProfile(
            system_prompt="Work as the site assistant.", model=f"{ENTRY}:research-1"
        )
        cfg.default_agent = AGENT
        cfg.external_access = ExternalAccessConfig(
            enabled=True, openai=Surface(enabled=True, allow_remote=False)
        )
        cfg.save()
        mutate_config(
            lambda doc: doc.setdefault("providers", []).append(
                {"name": ENTRY, "type": ENTRY, "model": "research-1"}
            )
        )
        save_active_models({"chat": [f"{ENTRY}:research-1"]})

        self.sessions = ConversationDirectory(
            AppConfig.load(), provider_factory=create_provider_factory()
        )
        self.log = ConversationLog(base_dir=tmp_path / "history")
        self.state = ConsoleState(
            sessions=self.sessions, start_time=0.0, conversation_log=self.log
        )
        self.state.context_builder = PromptAssembler(
            memory=MemoryJournal(workspace=tmp_path / "ws"),
            skills=ProcedureLibrary(
                skills_path=tmp_path / "skills", install_builtins=False
            ),
            conversation_log=self.log,
        )
        self.state._hook_store = None
        self.state.broadcast_ws = lambda *a, **k: None
        self.state.push_sessions_update = lambda *a, **k: None
        # A surface serves only with a token of its own; each client then signs in with its own.
        auth.create_surface_token(dialect.OPENAI_SURFACE)

    async def start(self) -> None:
        async def model_response(request):
            body = await request.json()
            handed = body["messages"]
            marker = _asked_for(handed)
            if marker:
                self.asked.append(handed)
                self.was_asked.set()
                await self.hold.wait()
            answer = f"answered {marker}" if marker else "Local notes"
            common = {"id": "controlled-local", "created": 1, "model": "research-1"}
            if body.get("stream"):
                response = web.StreamResponse(
                    headers={"Content-Type": "text/event-stream"}
                )
                await response.prepare(request)
                for packet in [
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": answer},
                                "finish_reason": None,
                            }
                        ],
                    },
                    {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        "usage": {
                            "prompt_tokens": 3,
                            "completion_tokens": 2,
                            "total_tokens": 5,
                        },
                    },
                ]:
                    await response.write(
                        ("data: " + json.dumps(packet) + "\n\n").encode()
                    )
                await response.write(b"data: [DONE]\n\n")
                await response.write_eof()
                return response
            return web.json_response(
                {
                    **common,
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": answer},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 2,
                        "total_tokens": 5,
                    },
                }
            )

        model_app = web.Application()
        model_app.router.add_post("/v1/chat/completions", model_response)

        async def models(request):
            return web.json_response(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "research-1",
                            "object": "model",
                            "created": 1,
                            "owned_by": "local",
                            "context_window": 32768,
                        }
                    ],
                }
            )

        model_app.router.add_get("/v1/models", models)
        self.model_server = TestClient(TestServer(model_app))
        await self.model_server.start_server()
        self.model_url = str(self.model_server.make_url("/v1"))
        # The gateway's own route table, behind the boundary every route runs behind (it answers a
        # refused field): the routes a client is registered through, and the endpoint it then asks.
        token_auth.use_ephemeral_secret(b"openai-conversation-owner")
        token_auth.revoke_all_sessions()
        self.owner = token_auth.generate_token("continuity-owner", kind="desktop")
        app = web.Application(middlewares=[token_auth.token_auth_middleware(port=0)])
        app["port"] = 0
        app["allowed_origins"] = set()
        app["state"] = self.state
        dialect.register_routes(
            app,
            turn_runner=_run_chat_scoped,
            persist_turn=save_session_to_history,
            restore_session=resolve_session,
        )
        app.router.add_get("/api/chat/sessions", api_chat_sessions)
        app.router.add_get("/api/external-access", ea.api_external_access)
        app.router.add_post(CLIENTS, ea.api_external_access_client)
        app.router.add_post(
            CLIENTS + "/{client_id}/persistent-sessions",
            ea.api_external_access_client_persistent_sessions,
        )
        self.http = TestClient(TestServer(app))
        await self.http.start_server()

    async def register(self, *, surfaces: tuple[str, ...] = ("openai",), **fields: Any):
        """Register a client the way Settings documents it: ``(status, payload)``."""
        body = {"label": "notes app", "surfaces": list(surfaces), **fields}
        resp = await self.http.post(
            CLIENTS, json=body, headers={"Authorization": "Bearer " + self.owner}
        )
        return resp.status, await resp.json()

    async def choose(self, client_id: str, body: Any) -> tuple[int, dict]:
        """Change whether *client_id* keeps its conversation: ``(status, payload)``."""
        resp = await self.http.post(
            f"{CLIENTS}/{client_id}/persistent-sessions",
            json=body,
            headers={"Authorization": "Bearer " + self.owner},
        )
        return resp.status, await resp.json()

    async def listed(self, client_id: str) -> dict:
        """The client's row as Settings → External Access reads it."""
        resp = await self.http.get(
            "/api/external-access", headers={"Authorization": "Bearer " + self.owner}
        )
        assert resp.status == 200
        rows = (await resp.json())["clients"]
        return next(row for row in rows if row["client_id"] == client_id)

    async def post(
        self, token: str, text: str, *, user: str | None = TAG
    ) -> tuple[int, dict]:
        """One request to the endpoint, read to its end."""
        body: dict[str, Any] = {
            "model": AGENT,
            "messages": [{"role": "user", "content": text}],
        }
        if user is not None:
            body["user"] = user
        resp = await self.http.post(
            dialect.ROUTE_CHAT, json=body, headers={"Authorization": f"Bearer {token}"}
        )
        return resp.status, await resp.json()

    async def ask(
        self, token: str, text: str, *, user: str | None = TAG
    ) -> tuple[int, dict]:
        """One request, and every turn settled after it."""
        status, payload = await self.post(token, text, user=user)
        await self.settled()
        return status, payload

    async def settled(self) -> None:
        """Until no turn is running, its cleanup included."""
        for _ in range(1000):
            if not any(s.running for s in self.state._sessions.values()):
                return
            await asyncio.sleep(0.01)
        raise AssertionError("a turn never finished")

    def handed(self, marker: str) -> str:
        """Everything the model was handed to answer the request marked *marker*."""
        calls = [call for call in self.asked if _asked_for(call) == marker]
        assert len(calls) == 1, f"the model was asked {len(calls)} times for {marker}"
        return "\n".join(str(m.get("content") or "") for m in calls[0])

    async def close(self) -> None:
        self.hold.set()
        await self.http.close()
        for session in list(self.state._sessions.values()):
            task = session.task
            if task is not None and not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=10)
                except (
                    asyncio.CancelledError,
                    Exception,
                ):  # noqa: BLE001 - teardown only
                    pass
        await self.sessions.close_all()
        await self.model_server.close()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()


@pytest.mark.asyncio
async def test_actual_sdk_client_conversation_rounds_restart_and_refusals(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    for surface in _SURFACES:
        monkeypatch.delenv(f"GIDEON_INBOUND_{surface}_TOKEN", raising=False)
    caps.reset_for_tests()
    world = _World(tmp_path, monkeypatch)
    await world.start()
    sdk = None
    try:
        for headers in [{}, {"Authorization": "Bearer invalid-client"}]:
            denied = await world.http.post(
                dialect.ROUTE_CHAT,
                json={
                    "model": AGENT,
                    "messages": [
                        {"role": "user", "content": "Refuse before execution"}
                    ],
                },
                headers=headers,
            )
            assert denied.status in {401, 403}
        status, made = await world.register(persistent_sessions=True)
        assert status == 200, made
        sdk = AsyncOpenAI(
            api_key=made["token"],
            base_url=str(world.http.make_url("/v1")),
            max_retries=0,
        )

        async def ask(text, user=TAG):
            reply = await sdk.chat.completions.create(
                model=AGENT, messages=[{"role": "user", "content": text}], user=user
            )
            await world.settled()
            return reply

        await ask(FIRST)
        await ask(SECOND)
        assert FIRST_SECRET in world.handed("[q2]")
        assert "answered [q1]" in world.handed("[q2]")
        await ask("[q3] Separate person?", user="separate-person")
        assert FIRST_SECRET not in world.handed("[q3]")
        status, unchanged = await world.choose(
            made["client_id"], {"persistent_sessions": True}
        )
        assert status == 200
        await ask("[q4] Same round?")
        assert FIRST_SECRET in world.handed("[q4]")
        # Drain native sessions, create a new ConsoleState on the same durable native history.
        old = world.state
        await world.sessions.close_all()
        world.sessions = ConversationDirectory(
            AppConfig.load(), provider_factory=create_provider_factory()
        )
        world.state = ConsoleState(
            sessions=world.sessions, start_time=0.0, conversation_log=world.log
        )
        world.state.context_builder = old.context_builder
        world.state._hook_store = None
        world.http.app["state"] = world.state
        await ask("[q5] After restart?")
        assert FIRST_SECRET in world.handed("[q5]")
        world.was_asked.clear()
        world.hold.clear()
        inflight = asyncio.create_task(ask("[q6] Captured old round?"))
        await asyncio.wait_for(world.was_asked.wait(), 5)
        status, changed = await world.choose(
            made["client_id"], {"persistent_sessions": False}
        )
        assert status == 200
        world.hold.set()
        await inflight
        assert FIRST_SECRET in world.handed("[q6]")
        await ask("[q6b] New round answered alone?")
        assert FIRST_SECRET not in world.handed("[q6b]")
        await world.choose(made["client_id"], {"persistent_sessions": True})
        await ask("[q7] New kept round?")
        assert FIRST_SECRET not in world.handed("[q7]")
        status, refused = await world.choose(
            made["client_id"], {"persistent_sessions": "true"}
        )
        assert status == 400
        status, wrong = await world.register(
            surfaces=("mcp",), persistent_sessions=True
        )
        assert status == 400
        # Integration credentials never become owner settings permissions or private history readers.
        response = await world.http.get(
            "/api/chat/sessions", headers={"Authorization": "Bearer " + made["token"]}
        )
        assert response.status in {401, 403}
        # Integration credentials never become owner settings permissions.
        response = await world.http.post(
            CLIENTS + "/" + made["client_id"] + "/persistent-sessions",
            json={"persistent_sessions": False},
            headers={"Authorization": "Bearer " + made["token"]},
        )
        assert response.status in {401, 403}
        with pytest.raises(APIStatusError):
            await sdk.chat.completions.create(
                model="other-agent",
                messages=[{"role": "user", "content": "No privilege widening"}],
            )
        assert clients.load_clients()[made["client_id"]].conversation_round == 2
    finally:
        if sdk is not None:
            await sdk.close()
        await world.close()
        caps.reset_for_tests()


@pytest.mark.asyncio
async def test_actual_sdk_unfinished_restart_toggle_and_private_client_consumers(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    for surface in _SURFACES:
        monkeypatch.delenv(f"GIDEON_INBOUND_{surface}_TOKEN", raising=False)
    caps.reset_for_tests()
    world = _World(tmp_path, monkeypatch)
    await world.start()
    sdk = None
    try:
        status, made = await world.register(persistent_sessions=True, agent=AGENT)
        assert status == 200, made
        sdk = AsyncOpenAI(
            api_key=made["token"],
            base_url=str(world.http.make_url("/v1")),
            max_retries=0,
        )

        async def ask(text, user=TAG):
            reply = await sdk.chat.completions.create(
                model=AGENT, messages=[{"role": "user", "content": text}], user=user
            )
            await world.settled()
            return reply

        await ask(FIRST)
        # Drain native sessions, create a new ConsoleState on the same durable native history.
        old = world.state
        await world.sessions.close_all()
        world.sessions = ConversationDirectory(
            AppConfig.load(), provider_factory=create_provider_factory()
        )
        world.state = ConsoleState(
            sessions=world.sessions, start_time=0.0, conversation_log=world.log
        )
        world.state.context_builder = old.context_builder
        world.state._hook_store = None
        world.http.app["state"] = world.state
        await ask("[q5] After restart?")
        assert FIRST_SECRET in world.handed("[q5]")
        world.was_asked.clear()
        world.hold.clear()
        inflight = asyncio.create_task(ask("[q6] Captured old round?"))
        await asyncio.wait_for(world.was_asked.wait(), 5)
        status, changed = await world.choose(
            made["client_id"], {"persistent_sessions": False}
        )
        assert status == 200
        world.hold.set()
        await inflight
        assert FIRST_SECRET in world.handed("[q6]")
        await ask("[q6b] New round answered alone?")
        assert FIRST_SECRET not in world.handed("[q6b]")
        await world.choose(made["client_id"], {"persistent_sessions": True})
        await ask("[q7] New kept round?")
        assert FIRST_SECRET not in world.handed("[q7]")
        status, refused = await world.choose(
            made["client_id"], {"persistent_sessions": "true"}
        )
        assert status == 400
        status, wrong = await world.register(
            surfaces=("mcp",), persistent_sessions=True
        )
        assert status == 400
        # Integration credentials never become owner settings permissions or private history readers.
        response = await world.http.get(
            "/api/chat/sessions", headers={"Authorization": "Bearer " + made["token"]}
        )
        assert response.status in {401, 403}
        # Integration credentials never become owner settings permissions.
        response = await world.http.post(
            CLIENTS + "/" + made["client_id"] + "/persistent-sessions",
            json={"persistent_sessions": False},
            headers={"Authorization": "Bearer " + made["token"]},
        )
        assert response.status in {401, 403}
        with pytest.raises(APIStatusError):
            await sdk.chat.completions.create(
                model="other-agent",
                messages=[{"role": "user", "content": "No privilege widening"}],
            )
        assert clients.load_clients()[made["client_id"]].conversation_round == 2
        await ask("[qOwner] Untrusted user field is not owner identity.", user="owner")
        key = dialect.session_key_for(made["client_id"], "owner", conversation_round=2)
        resident = world.state._sessions[key]
        assert resident._initiator == {
            "kind": "bridge",
            "name": made["client_id"],
            "tenant": "",
        }
        ingress = next(
            row["meta"]["ingress"]
            for row in reversed(resident.messages)
            if row.get("role") == "user"
        )
        assert ingress["principal"] == resident._initiator
        owner_history = await world.http.get(
            "/api/chat/sessions", headers={"Authorization": "Bearer " + world.owner}
        )
        assert owner_history.status == 200
        # A registered native handler at the same path cannot acquire OpenAI delegation.
        guarded = web.Application(
            middlewares=[token_auth.token_auth_middleware(port=0)]
        )
        guarded["state"] = world.state
        guarded["port"] = 0
        guarded["allowed_origins"] = set()
        guarded.router.add_post(dialect.ROUTE_CHAT, ea.api_external_access)
        guarded.router.add_get(dialect.ROUTE_CHAT, dialect.handle_models)
        guarded.router.add_get("/v1/private-data", ea.api_external_access)
        probe = TestClient(TestServer(guarded))
        await probe.start_server()
        try:
            for method, path in [
                ("POST", dialect.ROUTE_CHAT),
                ("GET", dialect.ROUTE_CHAT),
                ("GET", "/v1/private-data"),
            ]:
                response = await probe.request(
                    method, path, headers={"Authorization": "Bearer " + made["token"]}
                )
                assert response.status in {401, 403}
        finally:
            await probe.close()
    finally:
        if sdk is not None:
            await sdk.close()
        await world.close()
        caps.reset_for_tests()


def test_round_count_and_session_names_are_disjoint():
    assert clients._round_of(True) == 0
    assert clients._round_of("2") == 0
    assert clients._round_of(-1) == 0
    assert clients._round_of(2) == 2
    assert (
        len(
            {
                dialect.session_key_for("client", "tag", conversation_round=i)
                for i in range(4)
            }
        )
        == 4
    )
