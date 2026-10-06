import asyncio
import json
import time
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.extensions.providers.use_cases import save_use_case_settings
from gideon.integrations.voice.duplex import (
    DEFAULT_CONFIRMATION_PHRASES,
    DEFAULT_EXIT_PHRASES,
    is_confirmation,
    is_exit,
)
from gideon.interfaces.dashboard import token_auth
from gideon.interfaces.dashboard.handlers.core import api_gideon_config_patch
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.interfaces.dashboard.voice_settings import register_voice_settings


def test_shared_phrase_vectors():
    root = Path(__file__).resolve().parents[2]
    cases = json.loads(
        (
            root / "apps/console/src/shared/ui/composer/duplexPhraseCases.json"
        ).read_text()
    )["cases"]
    assert len(cases) >= 20
    for case in cases:
        phrases = case.get("phrases", {})
        assert (
            is_confirmation(
                case["text"], phrases.get("confirmation", DEFAULT_CONFIRMATION_PHRASES)
            )
            == case["confirmation"]
        ), case["name"]
        assert (
            is_exit(case["text"], phrases.get("exit", DEFAULT_EXIT_PHRASES))
            == case["exit"]
        ), case["name"]


@pytest.mark.asyncio
async def test_committed_settings_and_owner_http_reach_real_socket(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    token_auth.use_ephemeral_secret(b"voice-live-owner")
    token_auth.revoke_all_sessions()
    owner = token_auth.generate_token("voice-owner", kind="desktop")
    state = ConsoleState(sessions=None, start_time=time.time())
    app = web.Application(middlewares=[token_auth.token_auth_middleware(port=0)])
    app["state"] = state
    app["port"] = 0
    app["allowed_origins"] = set()
    register_voice_settings(app)

    async def socket(request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        state.register_ws(ws)
        try:
            async for message in ws:
                pass
        finally:
            state.unregister_ws(ws)
        return ws

    app.router.add_get("/api/ws", socket)
    app.router.add_patch("/api/config/gideon", api_gideon_config_patch)
    client = TestClient(TestServer(app))
    await client.start_server()
    headers = {"Authorization": "Bearer " + owner}
    ws = await client.ws_connect("/api/ws", headers=headers)
    try:
        await asyncio.to_thread(
            save_use_case_settings, "tts", {"enabled": True, "auto_speak": True}
        )
        assert await ws.receive_json(timeout=2) == {
            "type": "refresh",
            "data": {"kinds": ["voice"]},
        }
        await asyncio.to_thread(save_use_case_settings, "chat", {"enabled": True})
        with pytest.raises(asyncio.TimeoutError):
            await ws.receive_json(timeout=0.05)
        response = await client.patch(
            "/api/config/gideon",
            json={"path": "voice.duplex_mute_enabled", "value": False},
            headers=headers,
        )
        assert response.status == 200, await response.text()
        assert await ws.receive_json(timeout=2) == {
            "type": "refresh",
            "data": {"kinds": ["voice"]},
        }
        denied = await client.patch(
            "/api/config/gideon",
            json={"path": "voice.invalid", "value": False},
            headers=headers,
        )
        assert denied.status == 400
        with pytest.raises(asyncio.TimeoutError):
            await ws.receive_json(timeout=0.05)
    finally:
        await ws.close()
        await client.close()
        await state.close_all_ws()
        token_auth.revoke_all_sessions()
        token_auth.use_persistent_secret()
