"""Room records and shared conversation journals."""

from __future__ import annotations

import builtins
import fcntl
import json
import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.cognition.history import ConversationLog
from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("invalid room or member id")
    return value


def session_key(room_id: str, member_id: str) -> str:
    return f"room:{_identifier(room_id)}:{_identifier(member_id)}"


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 500:
        raise ValueError(f"{name} must be a nonempty string of at most 500 characters")
    return value.strip()


@dataclass
class RoomMember:
    id: str
    agent: str
    name: str = ""
    role: str = ""
    listen_policy: str = "all"
    profile_narrowing: dict | None = None

    def __post_init__(self):
        _identifier(self.id)
        self.agent = _text(self.agent, "agent")
        self.name = _text(self.name or self.agent, "member name")
        if not isinstance(self.role, str) or len(self.role) > 4000:
            raise ValueError("role must be a string of at most 4000 characters")
        self.role = self.role.strip()
        if self.profile_narrowing is not None and not isinstance(
            self.profile_narrowing, dict
        ):
            raise ValueError("profile_narrowing must be an object")
        if self.listen_policy not in ("all", "mentions", "none"):
            raise ValueError("listen_policy must be all, mentions, or none")


@dataclass
class Room:
    id: str
    name: str
    members: builtins.list[RoomMember] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        payload = asdict(self)
        for member in payload["members"]:
            if member["profile_narrowing"] is None:
                del member["profile_narrowing"]
        return payload


class RoomStore:
    def __init__(self, home: Path | None = None):
        self.directory = (home or config_dir()) / "rooms"
        self.path = self.directory / "index.json"
        self.transcript = ConversationLog(self.directory / "transcripts")

    @contextmanager
    def _locked(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        with (self.directory / "index.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict[str, Room]:
        if not self.path.exists():
            return {}
        document = json.loads(self.path.read_text(encoding="utf-8"))
        return {
            item["id"]: Room(
                **{**item, "members": [RoomMember(**m) for m in item["members"]]}
            )
            for item in document["rooms"]
        }

    def _save(self, rooms: dict[str, Room]) -> None:
        atomic_write(
            self.path,
            json.dumps(
                {"version": 1, "rooms": [r.to_dict() for r in rooms.values()]},
                ensure_ascii=False,
            ),
            fsync=True,
            mode=0o600,
        )

    def list(self) -> builtins.list[Room]:
        return list(self._read().values())

    def get(self, room_id: str) -> Room:
        room = self._read().get(_identifier(room_id))
        if room is None:
            raise KeyError(room_id)
        return room

    @staticmethod
    def _members(
        members: builtins.list[dict], max_members: int
    ) -> builtins.list[RoomMember]:
        if not isinstance(members, list) or len(members) > max_members:
            raise ValueError(
                f"members must be an array with at most {max_members} entries"
            )
        result = []
        for member in members:
            if not isinstance(member, dict) or set(member) - {
                "id",
                "agent",
                "name",
                "role",
                "listen_policy",
                "profile_narrowing",
            }:
                raise ValueError("invalid member record")
            if "id" not in member or "agent" not in member:
                raise ValueError("member id and agent are required")
            result.append(RoomMember(**member))
        if len({m.id for m in result}) != len(result):
            raise ValueError("member ids must be unique")
        return result

    def create(
        self,
        name: object,
        members: builtins.list[dict] | None = None,
        *,
        max_members: int = 8,
    ) -> Room:
        now = datetime.now(timezone.utc).isoformat()
        room = Room(
            uuid4().hex,
            _text(name, "name"),
            self._members(members if members is not None else [], max_members),
            now,
            now,
        )
        with self._locked():
            rooms = self._read()
            rooms[room.id] = room
            self._save(rooms)
        return room

    def update(
        self,
        room_id: str,
        *,
        name: str | None = None,
        members: builtins.list[dict] | None = None,
        max_members: int = 8,
    ) -> Room:
        with self._locked():
            rooms = self._read()
            room = rooms[_identifier(room_id)]
            if name is not None:
                room.name = _text(name, "name")
            if members is not None:
                room.members = self._members(members, max_members)
            room.updated_at = datetime.now(timezone.utc).isoformat()
            self._save(rooms)
            return room

    def delete(self, room_id: str) -> None:
        with self._locked():
            rooms = self._read()
            del rooms[_identifier(room_id)]
            self._save(rooms)
            self.transcript.delete_session(room_id)

    def append(self, room_id: str, role: str, content: str, *, speaker: str) -> None:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("invalid room message role")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a nonempty string")
        with self._locked():
            room = self.get(room_id)
            if role == "assistant" and speaker not in {m.id for m in room.members}:
                raise ValueError("speaker must be a room member")
            self.transcript.append(
                room_id, role, content, speaker=_text(speaker, "speaker")
            )
            with self.transcript._path(room_id).open("rb") as stream:
                os.fsync(stream.fileno())

    def messages(self, room_id: str) -> builtins.list[dict]:
        self.get(room_id)
        return self.transcript.read_messages(room_id)
