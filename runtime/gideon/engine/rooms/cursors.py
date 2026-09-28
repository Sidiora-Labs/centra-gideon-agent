"""Atomic per-member positions in the append-only room transcript."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from gideon.core.atomic_write import atomic_write

if TYPE_CHECKING:
    from gideon.engine.rooms.store import RoomStore


class RoomCursorError(RuntimeError):
    pass


def cursors_path(store: RoomStore, room_id: str):
    from gideon.engine.rooms.store import _identifier
    return store.directory / "transcripts" / f"{_identifier(room_id)}.cursors.json"


def read_cursors(store: RoomStore, room_id: str) -> dict[str, int]:
    store.get(room_id)
    path = cursors_path(store, room_id)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise RoomCursorError("This room's member context cursors could not be read; the round was refused. Restore the cursor record or reset it explicitly before retrying.") from None
    if not isinstance(data, dict) or any(type(value) is not int or value < 0 for value in data.values()):
        raise RoomCursorError("This room's member context cursors are invalid; the round was refused. Restore the cursor record or reset it explicitly before retrying.")
    return data


def cursor_for(store: RoomStore, room_id: str, member_id: str) -> int:
    return read_cursors(store, room_id).get(member_id, 0)


def _advance_locked(store: RoomStore, room_id: str, member_id: str, offset: int) -> None:
    from gideon.engine.rooms.store import _identifier
    _identifier(member_id)
    if type(offset) is not int or not 0 <= offset <= len(store.messages(room_id)):
        raise ValueError("member cursor must be a valid transcript append offset")
    values = read_cursors(store, room_id)
    values[member_id] = offset
    atomic_write(cursors_path(store, room_id), json.dumps(values, sort_keys=True) + "\n", fsync=True, mode=0o600)


def advance(store: RoomStore, room_id: str, member_id: str, offset: int) -> None:
    with store._locked():
        _advance_locked(store, room_id, member_id, offset)


def member_feed(messages: list[dict], read_from: int, member_id: str, *, remembers: bool) -> tuple[list[dict], bool]:
    if not remembers or not 0 < read_from <= len(messages):
        return list(messages), False
    return [row for row in messages[read_from:] if not (row.get("role") == "assistant" and row.get("speaker") == member_id)], True
