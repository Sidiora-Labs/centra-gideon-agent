"""One WebSocket producer, one permission gate — and an unmapped note never reaches a client.

`broadcast_ws` enforces per-app `permissions.events`: an app-scoped socket receives an event only
if the app's manifest declared it. `_broadcast` — the always-on dashboard translator (sessions,
titles, refresh hints, chat messages, notifications) — ended in `self._send_ws_all(...)`, which
writes to every socket directly. So the gate sat immediately below a second producer that skipped
it, and app-scoped sockets received every always-on frame regardless of what the app declared.

Its `else:` branch was the second half of the defect: any unrecognized `_type` was shipped as
`{"type": "notification", "data": <the raw internal note>}`, so a typo'd or retired type reached
every client as an untyped blob the frontend renders as a toast.

Both are asserted here **at the socket**, by reading what a fake app-scoped WS actually received —
not by inspecting the producer. And the vacuity case is the one that matters: a DECLARED event must
still arrive, or "filter everything" would pass every test above it.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


class _FakeWS:
    """Enough WebSocketResponse surface for the broadcast path, plus a sent-frame log."""

    def __init__(self):
        self.closed = False
        self.sent: list[str] = []

    def send_str(self, msg: str):
        self.sent.append(msg)

        async def _noop():
            return None

        return _noop()

    def types(self) -> list[str]:
        return [json.loads(m).get("type") for m in self.sent]


@pytest.fixture
def state(monkeypatch, tmp_path):
    """A ConsoleState with the send path made synchronous and the real home untouched."""
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.interfaces.dashboard.state import ConsoleState

    st = ConsoleState.__new__(ConsoleState)
    st._ws_clients = []
    st._ws_app = {}
    st._notification_log = []
    monkeypatch.setattr(
        ConsoleState,
        "_schedule_ws_send",
        lambda self, coro: (coro.close(), True)[1],
        raising=False,
    )
    monkeypatch.setattr(
        ConsoleState, "_remove_ws", lambda self, ws: None, raising=False
    )
    return st


def _app_socket(state, app: str, declared_events: list[str], monkeypatch):
    """Register an app-scoped socket whose manifest declares exactly `declared_events`."""
    ws = _FakeWS()
    state._ws_clients.append(ws)
    state._ws_app[ws] = app

    class _Checker:
        def can_use_event(self, event_type: str) -> bool:
            return event_type in declared_events

    monkeypatch.setattr(
        "gideon.extensions.apps.permissions.checker_for",
        lambda _name: _Checker(),
        raising=False,
    )
    return ws


def test_an_always_on_frame_is_now_gated_for_an_app_socket(state, monkeypatch):
    """The defect: `_broadcast`'s frames used to reach app sockets ungated."""
    ws = _app_socket(state, "an-app", ["chat_message"], monkeypatch)

    state._broadcast({"_type": "sessions", "sessions": json.dumps([{"key": "s1"}])})

    assert (
        ws.types() == []
    ), f"an undeclared always-on frame reached an app socket: {ws.types()}"


def test_a_DECLARED_event_still_arrives(state, monkeypatch):
    """Vacuity. A gate that drops everything would satisfy every other test here."""
    ws = _app_socket(state, "an-app", ["chat_message"], monkeypatch)

    state._broadcast(
        {"_type": "chat_message", "session": "s1", "role": "assistant", "content": "hi"}
    )

    assert ws.types() == [
        "chat_message"
    ], f"a declared event was filtered out: {ws.types()}"
    body = json.loads(ws.sent[0])
    assert body["data"]["content"] == "hi", "the frame arrived but lost its payload"


def test_the_owner_dashboard_socket_still_gets_everything(state, monkeypatch):
    """An unscoped (owner) connection is not an app and must not be filtered."""
    owner = _FakeWS()
    state._ws_clients.append(owner)
    _app_socket(state, "an-app", [], monkeypatch)

    state._broadcast({"_type": "refresh", "kinds": "crons,cron_history"})

    assert owner.types() == [
        "refresh"
    ], f"the owner socket lost a frame: {owner.types()}"


def test_an_unmapped_note_type_is_dropped_rather_than_shipped(
    state, monkeypatch, caplog
):
    """The `else:` half: a raw internal note must never reach a client as a notification."""
    import logging

    owner = _FakeWS()
    state._ws_clients.append(owner)

    with caplog.at_level(logging.ERROR, logger="gideon.interfaces.dashboard.state"):
        state._broadcast(
            {"_type": "definitely_not_a_type", "secret_internal_field": "leak-me"}
        )

    assert owner.sent == [], f"an unmapped note was broadcast anyway: {owner.sent}"
    assert any(
        "unmapped _type" in r.message for r in caplog.records
    ), f"the drop was silent: {[r.message for r in caplog.records]}"


def test_a_notification_note_still_rides_the_wire(state):
    """A note with NO `_type` is the notify() path — explicit now, not a default branch."""
    owner = _FakeWS()
    state._ws_clients.append(owner)

    state._broadcast({"title": "Build finished", "body": "all green"})

    assert owner.types() == ["notification"]
    assert json.loads(owner.sent[0])["data"]["title"] == "Build finished"


def test_the_sessions_envelope_keys_survive_the_refactor(state):
    """`yolo`/`channelTrusted` are top-level envelope keys; a permissions fix must not eat them."""
    owner = _FakeWS()
    state._ws_clients.append(owner)

    state._broadcast(
        {
            "_type": "sessions",
            "sessions": json.dumps([]),
            "_yolo": True,
            "channelTrusted": True,
        }
    )

    body = json.loads(owner.sent[0])
    assert body["yolo"] is True and body["channelTrusted"] is True, body


def test_extra_cannot_relabel_the_frame_a_client_receives(state, monkeypatch):
    """`extra` merges envelope keys only — `type` stays owned by the producer.

    Written the way it is because the obvious version was VACUOUS: the permission check reads the
    `msg_type` ARGUMENT, so an undeclared event is dropped whatever `extra` says, and the test
    passed with the guard removed. The real property is what the client is TOLD a delivered frame
    is: a socket must never receive a `sessions` payload labelled `chat_message`, or every
    consumer that branches on `type` is reading a lie.
    """
    ws = _app_socket(state, "an-app", ["sessions"], monkeypatch)

    state.broadcast_ws(
        "sessions", [{"key": "s1"}], extra={"type": "chat_message", "yolo": True}
    )

    assert ws.types() == [
        "sessions"
    ], f"`extra` relabelled a delivered frame: {ws.types()}"
    assert (
        json.loads(ws.sent[0])["yolo"] is True
    ), "a legitimate envelope key was dropped"


def test_every_note_type_in_the_tree_is_mapped():
    """The rail that makes an unmapped type a failing BUILD rather than a mystery frame.

    Scans `src/` for `"_type": "<value>"` literals and requires each to be translatable. Without
    it, the drop above is only discovered by someone noticing a missing toast in production.
    """
    import re
    from pathlib import Path

    from gideon.interfaces.dashboard.state import BROADCAST_NOTE_TYPES

    src = Path(__file__).resolve().parent.parent.parent / "src"
    found: dict[str, str] = {}
    for path in src.rglob("*.py"):
        for m in re.finditer(
            r'"_type":\s*"([a-z_]+)"', path.read_text(encoding="utf-8")
        ):
            found.setdefault(m.group(1), str(path.relative_to(src)))
    assert found, "the scan found no `_type` literals at all — it would pass vacuously"
    unmapped = {k: v for k, v in found.items() if k not in BROADCAST_NOTE_TYPES}
    unmapped = {k: v for k, v in unmapped.items() if k not in ("metadata", "archive")}
    assert not unmapped, f"note types no producer can translate: {unmapped}"


# --- BACKLOG-7: every app-scoped delivery, not just the broadcast fan-out -------------
#
# `broadcast_ws` was gated; the other three ways a frame reaches a socket were not. The
# per-connection replays in `ws.py` wrote `ws.send_json` directly (the sessions snapshot and
# the log-ring replay), the live-log push in `handlers/updates.py` wrote `ws.send_str`
# directly, and `broadcast_ws_subagent_subscribers` looped its subscriber set with no app
# check at all — so an app socket that declared nothing still received session details,
# every buffered log line, every subsequent log line, and every subagent chunk.
#
# These drive the REAL socket: a real `/api/ws` upgrade against a real ConsoleState, a real
# installed manifest on disk (so `checker_for` resolves it for real), and the real
# `_RingLogHandler`. The sentinel event is the vacuity guard — a declared event the app IS
# allowed to see, broadcast last, so "received nothing" is distinguishable from "socket
# never worked".

_SENTINEL = "sentinel_event"


def _install_app(tmp_path: Path, name: str, events: list[str]) -> None:
    """Write a REAL app manifest so `checker_for(name)` resolves a real checker."""
    appdir = tmp_path / "apps" / name
    appdir.mkdir(parents=True, exist_ok=True)
    (appdir / "app.json").write_text(
        json.dumps(
            {
                "name": name,
                "version": "1.0.0",
                "displayName": "Demo",
                "description": "x",
                "permissions": {"events": events},
            }
        ),
        encoding="utf-8",
    )
    (appdir / "installed.json").write_text(
        json.dumps({"name": name, "version": "1.0.0", "enabled": True}),
        encoding="utf-8",
    )


@asynccontextmanager
async def _socket(tmp_path, *, app_identity: str, events: list[str]):
    """Yield ``(state, socket, ring_handler)`` for one real /api/ws connection.

    ``app_identity=""`` is the owner/dashboard connection. Anything else is scoped to the
    installed app of that name, exactly as ``register_ws(ws, app=request['app'])`` does in
    production; the stub middleware here stands in for the token middleware that sets
    ``request['app']`` (mirroring ``test_app_permissions._client``).
    """
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.extensions.apps import manager
    from gideon.interfaces.dashboard.handlers.updates import _log_ring, _RingLogHandler
    from gideon.interfaces.dashboard.state import ConsoleState
    from gideon.interfaces.dashboard.ws import api_ws

    if app_identity:
        _install_app(tmp_path, app_identity, events)

    state = ConsoleState(sessions=ConversationDirectory(AppConfig.load()), start_time=0)
    state.get_or_create_session("gate-session")
    _log_ring.clear()
    _log_ring.append(json.dumps({"level": "INFO", "msg": "replayed-log-line"}))

    @web.middleware
    async def stub_identity(request, handler):
        request["app"] = app_identity
        return await handler(request)

    app = web.Application(middlewares=[stub_identity])
    app["state"] = state
    allowed_origins: set[str] = set()
    app["allowed_origins"] = allowed_origins
    app.router.add_get("/api/ws", api_ws)

    handler = _RingLogHandler(_log_ring)
    handler.setFormatter(logging.Formatter("%(message)s"))
    with (
        patch("gideon.core.config.loader.config_dir", return_value=tmp_path),
        patch.object(manager, "config_dir", return_value=tmp_path),
    ):
        async with TestClient(TestServer(app)) as client:
            origin = str(client.make_url("/").origin())
            allowed_origins.add(origin)
            async with client.ws_connect("/api/ws", origin=origin) as socket:
                handler.set_state(state)
                yield state, socket, handler
    _log_ring.clear()


async def _wait_for(pred, timeout: float = 3.0) -> None:
    """Wait until the server has processed a control frame (a subscription landed)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if pred():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the server never processed the subscription")


def _live_log(handler, text: str) -> None:
    handler.emit(
        logging.LogRecord("gideon", logging.INFO, __file__, 1, text, None, None)
    )


async def _drive_all_four_deliveries(state, socket, handler) -> None:
    """Trigger session details, log replay, live logs and subagent events, then a sentinel."""
    await socket.send_json({"type": "subscribe_logs"})
    await _wait_for(lambda: bool(state._ws_log_subscribers))
    _live_log(handler, "live-log-line")
    await socket.send_json({"type": "subscribe_subagents"})
    await _wait_for(lambda: bool(state._ws_subagent_subscribers))
    state.broadcast_ws_subagent_subscribers("subagent_chunk", {"id": "a1"})
    state.broadcast_ws(_SENTINEL, {"ok": True})


async def _frames_until_sentinel(socket) -> list[dict]:
    """Every frame the socket received up to and including the sentinel."""
    received: list[dict] = []
    while True:
        frame = await socket.receive_json(timeout=5)
        received.append(frame)
        if frame.get("type") == _SENTINEL:
            return received


@pytest.mark.asyncio
async def test_an_app_without_the_permission_receives_none_of_the_four_deliveries(
    tmp_path,
):
    async with _socket(tmp_path, app_identity="demo", events=[_SENTINEL]) as (
        state,
        socket,
        handler,
    ):
        await _drive_all_four_deliveries(state, socket, handler)
        frames = await _frames_until_sentinel(socket)

    assert [f["type"] for f in frames] == [
        _SENTINEL
    ], f"an undeclared delivery reached an app socket: {[f['type'] for f in frames]}"


@pytest.mark.asyncio
async def test_an_app_with_the_permission_receives_all_four_deliveries(tmp_path):
    """Vacuity: the gate must not be 'drop everything for apps'."""
    async with _socket(
        tmp_path,
        app_identity="demo",
        events=["sessions", "log", "subagent_chunk", _SENTINEL],
    ) as (state, socket, handler):
        await _drive_all_four_deliveries(state, socket, handler)
        frames = await _frames_until_sentinel(socket)

    types = [f["type"] for f in frames]
    assert types[0] == "sessions", f"the session-detail snapshot was lost: {types}"
    assert [s["key"] for s in frames[0]["data"]] == ["gate-session"]
    logs = [f["data"]["msg"] for f in frames if f["type"] == "log"]
    assert "replayed-log-line" in logs, f"the log replay was lost: {types}"
    assert "live-log-line" in logs, f"the live log push was lost: {types}"
    assert "subagent_chunk" in types, f"the subagent delivery was lost: {types}"


@pytest.mark.asyncio
async def test_the_owner_socket_receives_all_four_without_declaring_anything(tmp_path):
    """The owner connection is not app-scoped and must keep its full behaviour."""
    async with _socket(tmp_path, app_identity="", events=[]) as (
        state,
        socket,
        handler,
    ):
        await _drive_all_four_deliveries(state, socket, handler)
        frames = await _frames_until_sentinel(socket)

    types = [f["type"] for f in frames]
    assert types[0] == "sessions", types
    logs = [f["data"]["msg"] for f in frames if f["type"] == "log"]
    assert {"replayed-log-line", "live-log-line"} <= set(logs), logs
    assert "subagent_chunk" in types, types


def test_every_dashboard_ws_send_goes_through_the_chokepoint():
    """The rail: no second write path can reappear below the gate.

    Any ``ws.send_str``/``send_json``/``send_bytes`` inside the dashboard package must live
    in ``ConsoleState.deliver_ws`` or ``ConsoleState.send_ws_event`` — the two halves of the
    one gated write (scheduled and awaited). ``handlers/terminal.py`` is the single
    exemption and a deliberate one: its socket is a per-session PTY stream on its own
    endpoint, never registered with ``register_ws``, so it carries no event and is gated by
    the API permission instead.
    """
    import ast

    root = (
        Path(__file__).resolve().parents[2]
        / "runtime"
        / "gideon"
        / "interfaces"
        / "dashboard"
    )
    allowed = {
        ("state.py", "deliver_ws"),
        ("state.py", "send_ws_event"),
    }
    exempt_files = {"terminal.py"}
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        if path.name in exempt_files:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        scopes: dict[int, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for child in ast.walk(node):
                    scopes.setdefault(id(child), node.name)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(
                node.func, ast.Attribute
            ):
                continue
            if node.func.attr not in ("send_str", "send_json", "send_bytes"):
                continue
            scope = scopes.get(id(node), "<module>")
            if (path.name, scope) in allowed:
                continue
            offenders.append(f"{path.relative_to(root)}:{node.lineno} in {scope}()")
    assert not offenders, (
        "these WS sends bypass ConsoleState.deliver_ws/send_ws_event, so no app "
        "permission is checked on them:\n" + "\n".join(offenders)
    )
