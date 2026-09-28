"""App notifications and live chat events are filtered at their server consumers."""

import json
import time

import pytest

from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.server import start_dashboard


def _headers(token):
    from gideon.assurance.api_version import VERSION_HEADER, API_VERSION

    return {"Authorization": f"Bearer {token}", VERSION_HEADER: str(API_VERSION)}


def _install_app(home, name):
    app_dir = home / "apps" / name
    app_dir.mkdir(parents=True)
    (app_dir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": name,
                "description": "event isolation fixture",
                "permissions": {
                    "api": ["/api/notifications", "/api/ws"],
                    "events": ["chat_message", "notification", "notification_ack"],
                },
            }
        ),
        encoding="utf-8",
    )
    (app_dir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_app_notifications_and_websocket_delivery_are_creator_scoped(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    _install_app(home, "app-a")
    _install_app(home, "app-b")
    state = None
    runner, state = await start_dashboard(
        sessions=ConversationDirectory(AppConfig.load()), port=0
    )
    state.get_or_create_session("a-chat", app="app-a")
    state.get_or_create_session("b-chat", app="app-b")
    state.get_or_create_session("owner-chat")
    for session, title in (
        ("a-chat", "A notice"),
        ("b-chat", "B notice"),
        ("owner-chat", "Owner notice"),
    ):
        state.notify("chat_message", title, title, meta={"session": session})

    from aiohttp import ClientSession
    from gideon.interfaces.dashboard.token_auth import generate_token

    app_a_token = generate_token("owner", app="app-a")
    app_b_token = generate_token("owner", app="app-b")
    owner_token = generate_token("owner")
    port = runner.addresses[0][1]
    base = f"http://127.0.0.1:{port}"
    ws_base = f"ws://127.0.0.1:{port}/api/ws"
    origin = "http://localhost:3000"
    try:
        async with ClientSession() as client:
            for token, expected in (
                (app_a_token, "A notice"),
                (app_b_token, "B notice"),
            ):
                response = await client.get(
                    f"{base}/api/notifications?app_token={token}",
                    headers=_headers(owner_token),
                )
                payload = await response.json()
                assert response.status == 200
                assert [row["title"] for row in payload["notifications"]] == [expected]
                assert payload["unread"] == 1

            denied = await client.post(
                f"{base}/api/notifications/ack?app_token={app_a_token}",
                json={
                    "ts": next(
                        note["ts"]
                        for note in state._notification_log
                        if note.get("title") == "B notice"
                    )
                },
                headers={**_headers(owner_token), "Origin": origin},
            )
            assert denied.status == 200
            assert (await denied.json())["ok"] is False
            accepted = await client.post(
                f"{base}/api/notifications/ack?app_token={app_a_token}",
                json={
                    "ts": next(
                        note["ts"]
                        for note in state._notification_log
                        if note.get("title") == "A notice"
                    )
                },
                headers={**_headers(owner_token), "Origin": origin},
            )
            assert (await accepted.json())["ok"] is True

            owner_ws = await client.ws_connect(
                ws_base,
                headers={**_headers(owner_token), "Origin": origin},
            )
            a_ws = await client.ws_connect(
                f"{ws_base}?app_token={app_a_token}",
                headers={**_headers(owner_token), "Origin": origin},
            )
            b_ws = await client.ws_connect(
                f"{ws_base}?app_token={app_b_token}",
                headers={**_headers(owner_token), "Origin": origin},
            )
            owner_initial = await owner_ws.receive_json()
            a_initial = await a_ws.receive_json()
            b_initial = await b_ws.receive_json()
            assert owner_initial["type"] == a_initial["type"] == b_initial["type"] == "sessions"
            assert {row["key"] for row in owner_initial["data"]} == {
                "a-chat", "b-chat", "owner-chat"
            }
            assert {row["key"] for row in a_initial["data"]} == {"a-chat"}
            assert {row["key"] for row in b_initial["data"]} == {"b-chat"}

            for session, content in (
                ("owner-chat", "owner event"),
                ("a-chat", "A event"),
                ("b-chat", "B event"),
            ):
                state.broadcast_ws(
                    "chat_message", {"session": session, "content": content}
                )

            owner_events = [(await owner_ws.receive_json()) for _ in range(3)]
            a_event = await a_ws.receive_json()
            b_event = await b_ws.receive_json()
            assert {row["data"]["content"] for row in owner_events} == {
                "owner event", "A event", "B event"
            }
            assert a_event["data"]["content"] == "A event"
            assert b_event["data"]["content"] == "B event"
            await owner_ws.close()
            await a_ws.close()
            await b_ws.close()
    finally:
        await runner.cleanup()
