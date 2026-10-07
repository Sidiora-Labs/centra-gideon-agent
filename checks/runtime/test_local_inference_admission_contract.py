"""Local transport turns, owner-only waiting, and unsent move-on over real HTTP."""

import asyncio
from types import SimpleNamespace

import pytest
import test_provider_locality_health_contract as locality_fixtures
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.model_registry import (
    api_local_inference_move_on,
    api_local_inference_waits,
)
from gideon.interfaces.dashboard.ws_state import WebSocketState
from gideon.security.guardrails import local_inference as q

serving = locality_fixtures.serving


@pytest.mark.asyncio
async def test_real_transport_priority_owner_api_move_cancel_and_locality(serving):
    registry, _, _, endpoint = serving
    await registry.build_catalog(registry.get_entry("Local")).list_models()
    from gideon.integrations.llm.registry import ProviderEntry

    registry.register_entry(
        ProviderEntry(
            "Alias",
            "ollama",
            "regular:8b",
            {"endpoint": endpoint.replace("127.0.0.1", "localhost")},
        )
    )
    assert q.resource_key("Local", "regular:8b") == q.resource_key(
        "Alias", "regular:8b"
    )
    assert q.resource_key("LAN", "regular:8b") == ""
    assert q.resource_key("Local", "forwarded:8b") == ""
    assert q.resource_key("Local", "regular:8b") != q.resource_key("Local", "other:8b")
    calls, started, release = [], asyncio.Event(), asyncio.Event()

    async def inference(request):
        calls.append(request.match_info["label"])
        if calls[-1] == "holder":
            started.set()
            await release.wait()
        return web.json_response({"ok": True})

    @web.middleware
    async def identity(request, handler):
        identity = request.headers.get("Test-Identity", "")
        if identity == "owner":
            request["user"] = "owner"
        if identity == "app":
            request["app"] = "untrusted"
        return await handler(request)

    app = web.Application(middlewares=[identity])
    app["local_secret"] = "test-secret"
    app.router.add_post("/inference/{label}", inference)
    app.router.add_get("/waits", api_local_inference_waits)
    app.router.add_post("/waits/{wait_id}/move-on", api_local_inference_move_on)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"
    tasks = []
    async with ClientSession() as client:

        async def call(label, attended=False, next_ref=""):
            with (
                q.attending(
                    q.Attended("Answering your chat", "private-session")
                    if attended
                    else None
                ),
                q.next_entry(next_ref),
            ):
                async with q.turn("Local", "regular:8b", within=2):
                    async with client.post(base + "/inference/" + label) as response:
                        assert response.status == 200

        try:
            holder = asyncio.create_task(call("holder"))
            tasks.append(holder)
            await started.wait()
            background = asyncio.create_task(call("background"))
            tasks.append(background)
            owner = asyncio.create_task(call("owner", True))
            tasks.append(owner)
            moved = asyncio.create_task(call("moved", True, "LAN:regular:8b"))
            tasks.append(moved)
            cancelled = asyncio.create_task(call("cancelled", True))
            tasks.append(cancelled)
            await asyncio.sleep(0.01)
            for headers in (
                {},
                {"Test-Identity": "app"},
                {"X-Internal-Secret": "test-secret", "X-Session-Key": "agent"},
            ):
                async with client.get(base + "/waits", headers=headers) as response:
                    assert response.status == 403
            async with client.get(
                base + "/waits", headers={"Test-Identity": "owner"}
            ) as response:
                rows = (await response.json())["waits"]
                assert len(rows) == 3
                assert "private-session" not in str(rows) and all(
                    row["holder"] == "background work" for row in rows
                )
            row = next(row for row in rows if row["next_ref"])
            async with client.post(
                base + "/waits/" + row["id"] + "/move-on",
                headers={"Test-Identity": "app"},
            ) as response:
                assert response.status == 403
            async with client.post(
                base + "/waits/" + row["id"] + "/move-on",
                headers={"Test-Identity": "owner"},
            ) as response:
                assert response.status == 200
            with pytest.raises(q.LocalInferenceBusy):
                await moved
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert calls == ["holder"]
            release.set()
            await asyncio.gather(holder, owner, background)
            assert calls == ["holder", "owner", "background"]
            assert not q.waits() and not q._RESOURCES
            assert not q.move_on(row["id"])
        finally:
            release.set()
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await runner.cleanup()


@pytest.mark.asyncio
async def test_timeout_unsent_and_owner_socket_signal(serving):
    holder = await q.acquire("Local", "regular:8b")
    try:
        with q.attending(q.Attended("Reply")), q.next_entry("LAN:regular:8b"):
            with pytest.raises(q.LocalInferenceBusy):
                await q.acquire("Local", "regular:8b", within=0.01)
        assert not q.waits()
        assert q._RESOURCES[holder.key].active is holder
    finally:
        q._release(holder)

    class Socket:
        closed = False

        def __init__(self):
            self.messages = []

        async def send_str(self, message):
            self.messages.append(message)

    state = WebSocketState()
    state._ws_clients = []
    state._ws_app = {}
    state._ws_owner = set()
    state._ws_loop = None
    state._ws_log_subscribers = set()
    state._ws_subagent_subscribers = set()
    owner, agent, app = Socket(), Socket(), Socket()
    state.register_ws(owner, owner=True)
    state.register_ws(agent)
    state.register_ws(app, app="untrusted")
    state.broadcast_ws("local_inference_waits", {}, owner_only=True)
    await asyncio.sleep(0.01)
    assert len(owner.messages) == 1 and not agent.messages and not app.messages
    state.unregister_ws(owner)
    assert owner not in state._ws_owner


@pytest.mark.asyncio
async def test_one_resource_across_native_event_loops_and_owner_binding(serving):
    from gideon.security.approval_answer import YOU
    from gideon.security.approval_answer import app as app_principal
    from gideon.security.session_credentials import begin_turn, end_turn

    holder = await q.acquire("Local", "regular:8b")
    finished = []

    def other_loop():
        async def request():
            async with q.turn("AliasMissing", "regular:8b"):
                pass
            async with q.turn("Local", "regular:8b", within=2):
                finished.append("other-loop")

        asyncio.run(request())

    task = asyncio.create_task(asyncio.to_thread(other_loop))
    for _ in range(100):
        if q._RESOURCES[holder.key].background:
            break
        await asyncio.sleep(0.005)
    assert not finished and q._RESOURCES[holder.key].background
    q._release(holder)
    await task
    assert finished == ["other-loop"] and not q._RESOURCES
    model = SimpleNamespace(
        served_model_ref="Local:regular:8b", first_token_timeout_secs=1
    )
    runtime = SimpleNamespace(
        _model=model,
        _preferred_model_ref="Local:regular:8b",
        _definition=SimpleNamespace(model=""),
        _active_fallback=None,
        _session_key="owner-session",
        _unattended=False,
        _announce_failover=False,
    )
    holder = await q.acquire("Local", "regular:8b")

    async def native_request(principal, created_by_app=""):
        credential = begin_turn(
            "owner-session", principal, turn_id="turn", created_by_app=created_by_app
        )
        try:
            async with q.native_turn(runtime):
                pass
        finally:
            end_turn(credential)

    owner = asyncio.create_task(native_request(YOU))
    await asyncio.sleep(0.01)
    assert len(q.waits()) == 1
    owner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await owner
    app_task = asyncio.create_task(
        native_request(app_principal("untrusted"), "untrusted")
    )
    await asyncio.sleep(0.01)
    assert not q.waits()
    app_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await app_task
    q._release(holder)
    assert not q._RESOURCES
