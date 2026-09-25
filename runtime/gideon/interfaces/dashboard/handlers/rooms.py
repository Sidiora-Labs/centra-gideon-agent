"""Durable shared rooms and server-owned conversation turns."""

from dataclasses import asdict

from aiohttp import web

from gideon.core.config.document import write_configuration
from gideon.core.config.loader import AppConfig, ConfigPreserveError, config_path
from gideon.core.http_request import read_json_body
from gideon.engine.rooms.safety import resolved_posture
from gideon.engine.rooms.store import RoomStore, session_key
from gideon.engine.rooms.turn import RoomBusyError, RoomTurns
from gideon.http_download import download_headers
from gideon.interfaces.dashboard import session_export
from gideon.security.security import redact_field

_STORE = web.AppKey("room_store", RoomStore)
_TURNS = web.AppKey("room_turns", RoomTurns)


def _redacted(value):
    if isinstance(value, str):
        return redact_field(value)
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    if isinstance(value, dict):
        return {key: _redacted(item) for key, item in value.items()}
    return value


def _public_turn(turn):
    return (
        _redacted({key: value for key, value in turn.items() if key != "content_hash"})
        if turn
        else None
    )


def _turns(request: web.Request) -> RoomTurns:
    if _TURNS not in request.app:
        state = request.app.get("state")
        if state is None:
            raise web.HTTPServiceUnavailable(
                text='{"error":"room runtime unavailable"}',
                content_type="application/json",
            )
        request.app[_TURNS] = RoomTurns(request.app[_STORE], state)
    return request.app[_TURNS]


async def api_enable_rooms(request: web.Request) -> web.Response:
    try:
        body = await read_json_body(request)
        if body:
            raise ValueError("enable does not accept configuration fields")
        rooms = asdict(AppConfig.load().rooms)
        rooms["enabled"] = True
        write_configuration(config_path(), {"rooms": rooms}, ConfigPreserveError)
        return web.json_response({"enabled": True})
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except (ConfigPreserveError, OSError):
        return web.json_response(
            {"error": "Rooms could not be enabled; configuration was preserved"},
            status=500,
        )


async def api_rooms(request: web.Request) -> web.Response:
    config = AppConfig.load().rooms
    store = request.app[_STORE]
    room_id = request.match_info.get("room_id", "")
    action = request.match_info.route.name or ""
    try:
        if not room_id and request.method == "GET":
            return web.json_response(
                _redacted(
                    {
                        "rooms": (
                            [r.to_dict() for r in store.list()]
                            if config.enabled
                            else []
                        ),
                        "enabled": config.enabled,
                        "max_members": config.max_members,
                        "round_budget": config.round_budget,
                    }
                )
            )
        if not config.enabled:
            return web.json_response({"error": "rooms are disabled"}, status=404)
        if not room_id:
            body = await read_json_body(request)
            if set(body) - {"name", "members"}:
                raise ValueError("create accepts name and members")
            room = store.create(
                body.get("name"),
                body.get("members", []),
                max_members=config.max_members,
            )
            return web.json_response(_redacted({"room": room.to_dict()}), status=201)
        room = store.get(room_id)
        if action == "transcript":
            try:
                limit = int(request.query.get("limit", "100"))
            except ValueError:
                raise ValueError("limit must be between 1 and 500") from None
            if not 1 <= limit <= 500:
                raise ValueError("limit must be between 1 and 500")
            messages = store.messages(
                room_id, limit=limit + 1, before=request.query.get("before")
            )
            has_more = len(messages) > limit
            messages = messages[-limit:]
            return web.json_response(
                _redacted(
                    {
                        "messages": messages,
                        "has_more": has_more,
                        "before": messages[0]["id"] if has_more else None,
                    }
                )
            )
        if action == "turn":
            return web.json_response({"turn": _public_turn(store.turn(room_id))})
        if action == "turns":
            body = await read_json_body(request)
            if set(body) != {"text", "request_id"}:
                raise ValueError("turn requires text and request_id")
            turn = _turns(request).submit(room_id, body["text"], body["request_id"])
            return web.json_response({"turn": _public_turn(turn)}, status=202)
        if action == "cancel":
            turn = await _turns(request).cancel(room_id)
            return web.json_response({"turn": _public_turn(turn)})
        if action == "export":
            fmt = request.query.get("format", "json")
            text, content_type = session_export.render(
                fmt,
                title=room.name,
                key=room.id,
                meta=room.to_dict(),
                messages=store.messages(room_id),
            )
            return web.Response(
                text=text,
                content_type=content_type,
                headers=download_headers(
                    session_export.export_filename(room.name, room.id, fmt)
                ),
            )
        if action == "messages":
            body = await read_json_body(request)
            turns = _turns(request)
            content = body.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a nonempty string")
            messages = await turns.run(room_id, content)
            return web.json_response(_redacted({"messages": messages}))
        if request.method in {"PATCH", "DELETE"}:
            current = store.turn(room_id)
            if current and current["status"] in {"queued", "running"}:
                raise RoomBusyError("room already has an active turn")
        if request.method == "PATCH":
            body = await read_json_body(request)
            if set(body) - {"name", "members"} or not body:
                raise ValueError("update accepts name and members")
            if any(value is None for value in body.values()):
                raise ValueError("room fields cannot be null")
            room = store.update(room_id, **body, max_members=config.max_members)
        elif request.method == "DELETE":
            store.delete(room_id)
            return web.json_response({"ok": True})
        payload = room.to_dict()
        payload["member_postures"] = {
            member.id: resolved_posture(session_key(room.id, member.id))
            for member in room.members
        }
        return web.json_response(
            _redacted({"room": payload, "member_postures": payload["member_postures"]})
        )
    except KeyError:
        return web.json_response({"error": "room not found"}, status=404)
    except RoomBusyError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except PermissionError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


async def _cleanup_rooms(app: web.Application) -> None:
    turns = app.get(_TURNS)
    if turns:
        await turns.close()


def setup_room_routes(app: web.Application, store: RoomStore | None = None) -> None:
    app[_STORE] = store or RoomStore()
    app[_STORE].recover_interrupted()
    app.on_cleanup.append(_cleanup_rooms)
    app.router.add_get("/api/rooms", api_rooms, allow_head=False)
    app.router.add_post("/api/rooms", api_rooms)
    app.router.add_post("/api/rooms/enable", api_enable_rooms)
    app.router.add_get("/api/rooms/{room_id}", api_rooms, allow_head=False)
    app.router.add_patch("/api/rooms/{room_id}", api_rooms)
    app.router.add_delete("/api/rooms/{room_id}", api_rooms)
    app.router.add_get(
        "/api/rooms/{room_id}/transcript",
        api_rooms,
        allow_head=False,
        name="transcript",
    )
    app.router.add_post("/api/rooms/{room_id}/messages", api_rooms, name="messages")
    app.router.add_post("/api/rooms/{room_id}/turns", api_rooms, name="turns")
    app.router.add_post("/api/rooms/{room_id}/cancel", api_rooms, name="cancel")
    app.router.add_get(
        "/api/rooms/{room_id}/turn", api_rooms, allow_head=False, name="turn"
    )
    app.router.add_get(
        "/api/rooms/{room_id}/export", api_rooms, allow_head=False, name="export"
    )
