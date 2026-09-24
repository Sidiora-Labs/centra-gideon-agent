"""Kill-switched Agent Rooms endpoints."""

from aiohttp import web

from gideon.core.config.loader import AppConfig
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


async def api_rooms(request: web.Request) -> web.Response:
    config = AppConfig.load().rooms
    if not config.enabled:
        return web.json_response({"error": "rooms are disabled"}, status=404)
    store = request.app[_STORE]
    room_id = request.match_info.get("room_id", "")
    action = request.match_info.get("action", "")
    try:
        if not room_id:
            if request.method == "GET":
                return web.json_response(
                    _redacted({"rooms": [r.to_dict() for r in store.list()]})
                )
            body = await read_json_body(request)
            room = store.create(
                body.get("name"),
                body.get("members", []),
                max_members=config.max_members,
            )
            return web.json_response(_redacted({"room": room.to_dict()}), status=201)
        room = store.get(room_id)
        if action == "transcript":
            return web.json_response(_redacted({"messages": store.messages(room_id)}))
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
            if _TURNS not in request.app:
                state = request.app.get("state")
                if state is None:
                    return web.json_response(
                        {"error": "room runtime unavailable"}, status=503
                    )
                request.app[_TURNS] = RoomTurns(store, state)
            content = body.get("content")
            if not isinstance(content, str):
                raise ValueError("content must be a nonempty string")
            messages = await request.app[_TURNS].run(room_id, content)
            return web.json_response(_redacted({"messages": messages}))
        if request.method in {"PATCH", "DELETE"}:
            turns = request.app.get(_TURNS)
            if turns and turns.active(room_id):
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
        return web.json_response(_redacted({"room": payload}))
    except KeyError:
        return web.json_response({"error": "room not found"}, status=404)
    except RoomBusyError as exc:
        return web.json_response({"error": str(exc)}, status=409)
    except PermissionError as exc:
        return web.json_response({"error": str(exc)}, status=404)
    except ValueError as exc:
        return web.json_response({"error": str(exc)}, status=400)


def setup_room_routes(app: web.Application, store: RoomStore | None = None) -> None:
    app[_STORE] = store or RoomStore()
    app.router.add_get("/api/rooms", api_rooms, allow_head=False)
    app.router.add_post("/api/rooms", api_rooms)
    app.router.add_get("/api/rooms/{room_id}", api_rooms, allow_head=False)
    app.router.add_patch("/api/rooms/{room_id}", api_rooms)
    app.router.add_delete("/api/rooms/{room_id}", api_rooms)
    app.router.add_get(
        "/api/rooms/{room_id}/{action:transcript}", api_rooms, allow_head=False
    )
    app.router.add_post("/api/rooms/{room_id}/{action:messages}", api_rooms)
    app.router.add_get(
        "/api/rooms/{room_id}/{action:export}", api_rooms, allow_head=False
    )
