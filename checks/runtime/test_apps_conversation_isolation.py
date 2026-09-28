"""Creator-scoped app conversations survive persistence and listing."""

import json
import time

import pytest

from gideon.cognition.history import ConversationLog
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.permissions import route_authority
from gideon.interfaces.dashboard.chat_persistence import (
    save_session_to_history,
)
from gideon.interfaces.dashboard.server import start_dashboard


def _install_app(home, name):
    app_dir = home / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "conversation isolation fixture",
                "permissions": {
                    "api": ["/api/chat", "/api/chat/*", "/api/notifications"],
                    "events": ["sessions", "chat_message", "notification"],
                },
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )


async def _start_gateway(log):
    sessions = ConversationDirectory(AppConfig.load())
    return await start_dashboard(sessions=sessions, port=0, conversation_log=log)


def _headers(token):
    from gideon.assurance.api_version import VERSION_HEADER, API_VERSION

    return {
        "Authorization": f"Bearer {token}",
        VERSION_HEADER: str(API_VERSION),
    }


@pytest.mark.asyncio
async def test_creator_is_durable_and_app_listing_excludes_other_and_legacy_sessions(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    _install_app(home, "app-a")
    _install_app(home, "app-b")
    log = ConversationLog(base_dir=home / "history")
    runner, state = await _start_gateway(log)
    try:
        app_a = state.get_or_create_session("owned-a", app="app-a")
        app_a._app = "telegram"
        app_a.append("user", "A private prompt", "msg msg-u")
        state.get_or_create_session("owned-b", app="app-b").append(
            "user", "B private prompt", "msg msg-u"
        )
        state.get_or_create_session("owner-chat").append(
            "user", "owner private prompt", "msg msg-u"
        )
        legacy = state.get_or_create_session("legacy")
        legacy._app = "app-a"
        for session in state._sessions.values():
            save_session_to_history(state, session, force=True)
    finally:
        await runner.cleanup()

    runner, restarted = await _start_gateway(log)
    try:
        from aiohttp import ClientSession
        from gideon.interfaces.dashboard.token_auth import generate_token

        app_a_token = generate_token("owner", app="app-a")
        app_b_token = generate_token("owner", app="app-b")
        owner_token = generate_token("owner")
        port = runner.addresses[0][1]
        base = f"http://127.0.0.1:{port}"
        async with ClientSession() as client:
            response = await client.get(
                f"{base}/api/chat/sessions?app_token={app_a_token}",
                headers=_headers(owner_token),
            )
            rows = await response.json()
            assert response.status == 200
            assert {row["key"] for row in rows} == {"owned-a"}
            own_detail = await client.get(
                f"{base}/api/chat/sessions/owned-a?app_token={app_a_token}",
                headers=_headers(owner_token),
            )
            assert own_detail.status == 200
            assert "A private prompt" in json.dumps(await own_detail.json())
            for name in ("owned-b", "owner-chat", "legacy"):
                denied = await client.get(
                    f"{base}/api/chat/sessions/{name}?app_token={app_a_token}",
                    headers=_headers(owner_token),
                )
                assert denied.status == 403
            app_b_rows = await client.get(
                f"{base}/api/chat/sessions?app_token={app_b_token}",
                headers=_headers(owner_token),
            )
            assert {row["key"] for row in await app_b_rows.json()} == {"owned-b"}
            owner_rows = await client.get(
                f"{base}/api/chat/sessions", headers=_headers(owner_token)
            )
            assert {row["key"] for row in await owner_rows.json()} == {
                "owned-a", "owned-b", "owner-chat", "legacy"
            }
    finally:
        await runner.cleanup()

    assert route_authority("GET", "/api/chat/sessions/{session}") is not None
    assert route_authority("POST", "/api/chat/sessions/{session}/approve") is None
