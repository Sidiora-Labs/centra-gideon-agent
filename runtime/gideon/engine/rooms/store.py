"""Room records and shared conversation journals."""

from __future__ import annotations

import builtins
import fcntl
import hashlib
import json
import os
import re
import shutil
from collections import deque
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


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def message_text(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 16000:
        raise ValueError("text must be a nonempty string of at most 16000 characters")
    return value.strip()


class RoomBusyError(RuntimeError):
    pass


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
        if not isinstance(self.name, str):
            raise ValueError("member name must be a string")
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
        with self.acquire_turn(room_id), self._locked():
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
        with self.acquire_turn(room_id), self._locked():
            rooms = self._read()
            del rooms[_identifier(room_id)]
            self._save(rooms)
            self.transcript.delete_session(room_id)
            shutil.rmtree(self.directory / "turns" / room_id, ignore_errors=True)

    def append(
        self,
        room_id: str,
        role: str,
        content: str,
        *,
        speaker: str,
        turn_id: str | None = None,
        interrupted: bool = False,
    ) -> dict:
        if role not in {"user", "assistant", "system"}:
            raise ValueError("invalid room message role")
        if not isinstance(content, str) or not content.strip() or len(content) > 100000:
            raise ValueError(
                "content must be a nonempty string of at most 100000 characters"
            )
        with self._locked():
            room = self.get(room_id)
            if role == "assistant" and speaker not in {m.id for m in room.members}:
                raise ValueError("speaker must be a room member")
            return self._append(
                room,
                role,
                content,
                speaker=speaker,
                turn_id=turn_id,
                interrupted=interrupted,
            )

    def _append(
        self,
        room: Room,
        role: str,
        content: str,
        *,
        speaker: str,
        turn_id: str | None = None,
        interrupted: bool = False,
    ) -> dict:
        now = timestamp()
        record = {
            "id": uuid4().hex,
            "role": role,
            "content": content,
            "speaker": _text(speaker, "speaker"),
            "speaker_name": next(
                (m.name for m in room.members if m.id == speaker), speaker
            ),
            "ts": now,
            "created_at": now,
        }
        if turn_id:
            record["turn_id"] = turn_id
        if interrupted:
            record["interrupted"] = True
        path = self.transcript._path(room.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab+") as stream:
            os.chmod(path, 0o600)
            stream.seek(0, 2)
            if stream.tell():
                stream.seek(-1, 2)
                if stream.read(1) != b"\n":
                    stream.write(b"\n")
            else:
                stream.write(
                    (
                        json.dumps({"_type": "metadata", "created_at": now}) + "\n"
                    ).encode()
                )
            stream.write((json.dumps(record, ensure_ascii=False) + "\n").encode())
            stream.flush()
            os.fsync(stream.fileno())
        self.transcript._invalidate_cache(room.id)
        return record

    def messages(
        self, room_id: str, *, limit: int | None = None, before: str | None = None
    ) -> builtins.list[dict]:
        self.get(room_id)
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 501):
            raise ValueError("limit must be between 1 and 500")
        if before is not None:
            _identifier(before)
        rows: deque[dict] = deque(maxlen=limit)
        path = self.transcript._path(room_id)
        found = before is None
        if path.exists():
            with path.open(encoding="utf-8") as stream:
                for index, line in enumerate(stream):
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(row, dict) or row.get("role") not in {
                        "user",
                        "assistant",
                        "system",
                    }:
                        continue
                    row.setdefault(
                        "id",
                        hashlib.sha256(
                            f"{room_id}:{index}:{line}".encode()
                        ).hexdigest()[:32],
                    )
                    row.setdefault("created_at", row.get("ts", ""))
                    row.setdefault("speaker_name", row.get("speaker", row["role"]))
                    if row["id"] == before:
                        found = True
                        break
                    rows.append(row)
        if not found:
            raise ValueError("transcript cursor was not found")
        return list(rows)

    def _turn_path(self, room_id: str, turn_id: str) -> Path:
        return (
            self.directory
            / "turns"
            / _identifier(room_id)
            / (_identifier(turn_id) + ".json")
        )

    def turn(self, room_id: str, turn_id: str | None = None) -> dict | None:
        self.get(room_id)
        path = (
            self._turn_path(room_id, turn_id)
            if turn_id
            else self.directory / "turns" / room_id / "current.json"
        )
        if not path.exists():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
        if not turn_id:
            return self.turn(room_id, record["id"])
        return record

    def _save_turn(self, record: dict) -> None:
        path = self._turn_path(record["room_id"], record["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            path, json.dumps(record, ensure_ascii=False), fsync=True, mode=0o600
        )

    def begin_turn(
        self, room_id: str, content: str, request_id: str
    ) -> tuple[dict, bool]:
        content = message_text(content)
        _identifier(request_id)
        if request_id == "current":
            raise ValueError("reserved request id")
        with self._locked():
            room = self.get(room_id)
            existing = self.turn(room_id, request_id)
            digest = hashlib.sha256(content.encode()).hexdigest()
            if existing:
                if existing.get("content_hash") != digest:
                    raise RoomBusyError(
                        "request id was already used for different text"
                    )
                return existing, False
            current = self.turn(room_id)
            if current and current["status"] in {"queued", "running"}:
                raise RoomBusyError("room already has an active turn")
            now = timestamp()
            record = dict(
                id=request_id,
                room_id=room_id,
                status="queued",
                member_id=None,
                text="",
                error=None,
                created_at=now,
                updated_at=now,
                content_hash=digest,
            )
            self._save_turn(record)
            atomic_write(
                self._turn_path(room_id, "current"),
                json.dumps({"id": request_id}),
                fsync=True,
                mode=0o600,
            )
            try:
                self._append(room, "user", content, speaker="user", turn_id=request_id)
            except Exception:
                record.update(
                    status="failed",
                    error="Message could not be stored",
                    updated_at=timestamp(),
                )
                self._save_turn(record)
                raise
            return record, True

    def update_turn(self, room_id: str, turn_id: str, **changes) -> dict:
        with self._locked():
            record = self.turn(room_id, turn_id)
            if record is None:
                raise KeyError(turn_id)
            record.update(changes, updated_at=timestamp())
            self._save_turn(record)
            return record

    def acquire_turn(self, room_id: str):
        self.get(room_id)
        directory = self.directory / "turns" / _identifier(room_id)
        directory.mkdir(parents=True, exist_ok=True)
        lock = (directory / "run.lock").open("a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock.close()
            raise RoomBusyError("room already has an active turn") from None
        return lock

    def recover_interrupted(self) -> None:
        for room in self.list():
            current = self.turn(room.id)
            if not current or current["status"] not in {"queued", "running"}:
                continue
            try:
                lock = self.acquire_turn(room.id)
            except RoomBusyError:
                continue
            try:
                current = self.turn(room.id)
                if current and current["status"] in {"queued", "running"}:
                    self.update_turn(
                        room.id,
                        current["id"],
                        status="failed",
                        error="Room turn interrupted by a runtime restart. Send a new message to continue.",
                    )
            finally:
                lock.close()
