"""OpenClaw-compatible remote agent sessions with durable provenance."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import aiohttp

from gideon.integrations.llm.credentials import CredentialStore


class RemoteSessionError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code, self.status = code, status


@dataclass(frozen=True)
class Connection:
    id: str
    label: str
    base_url: str
    credential_ref: str
    agent_id: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _base_url(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("base_url must be an HTTP(S) URL")
    value = value.strip().rstrip("/") + "/"
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "base_url must be an HTTP(S) origin without credentials, query or fragment"
        )
    return value


def _text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n\n".join(filter(None, (_text(item) for item in value)))
    if isinstance(value, dict):
        for key in ("text", "content", "body", "message", "output_text"):
            if key in value:
                found = _text(value[key])
                if found:
                    return found
        return _text(value.get("parts") or value.get("output") or [])
    return ""


class RemoteSessionStore:
    def __init__(self, home: Path):
        root = Path(home) / "capabilities" / "platform"
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "remote-agent-sessions.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY,label TEXT NOT NULL,base_url TEXT NOT NULL,credential_ref TEXT NOT NULL,agent_id TEXT NOT NULL,created_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(connection_id TEXT NOT NULL,remote_id TEXT NOT NULL,title TEXT NOT NULL,status TEXT,last_message_at TEXT,seen_at TEXT NOT NULL,PRIMARY KEY(connection_id,remote_id));
            """)

    def _db(self):
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        return db

    def save_connection(self, payload: dict) -> dict:
        allowed = {"id", "label", "base_url", "credential_ref", "agent_id"}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError("Invalid connection fields")
        values = {key: str(payload.get(key) or "").strip() for key in allowed}
        if (
            not values["id"]
            or len(values["id"]) > 128
            or not values["label"]
            or not values["credential_ref"]
        ):
            raise ValueError("id, label and credential_ref are required")
        values["base_url"] = _base_url(values["base_url"])
        values["agent_id"] = values["agent_id"] or "main"
        with self._db() as db:
            db.execute(
                "INSERT INTO connections VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,base_url=excluded.base_url,credential_ref=excluded.credential_ref,agent_id=excluded.agent_id",
                (
                    values["id"],
                    values["label"],
                    values["base_url"],
                    values["credential_ref"],
                    values["agent_id"],
                    _now(),
                ),
            )
        return self.connection(values["id"]).__dict__

    def connection(self, connection_id: str) -> Connection:
        with self._db() as db:
            row = db.execute(
                "SELECT id,label,base_url,credential_ref,agent_id FROM connections WHERE id=?",
                (connection_id,),
            ).fetchone()
        if row is None:
            raise FileNotFoundError("Remote connection not found")
        return Connection(**dict(row))

    def connections(self) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT id,label,base_url,credential_ref,agent_id FROM connections ORDER BY label,id"
            ).fetchall()
        return [dict(row) for row in rows]

    def retain(self, connection_id: str, rows: list[dict]) -> list[dict]:
        stamp = _now()
        with self._db() as db:
            for row in rows:
                db.execute(
                    "INSERT INTO sessions VALUES(?,?,?,?,?,?) ON CONFLICT(connection_id,remote_id) DO UPDATE SET title=excluded.title,status=excluded.status,last_message_at=excluded.last_message_at,seen_at=excluded.seen_at",
                    (
                        connection_id,
                        row["id"],
                        row["title"],
                        row.get("status"),
                        row.get("last_message_at"),
                        stamp,
                    ),
                )
        return rows

    def retained(self, connection_id: str) -> list[dict]:
        with self._db() as db:
            rows = db.execute(
                "SELECT remote_id AS id,title,status,last_message_at,seen_at FROM sessions WHERE connection_id=? ORDER BY COALESCE(last_message_at,seen_at) DESC",
                (connection_id,),
            ).fetchall()
        return [
            dict(row) | {"connection_id": connection_id, "provenance": "remote"}
            for row in rows
        ]


class RemoteSessionBridge:
    def __init__(self, home: Path, *, store=None, credentials=None, timeout=15):
        self.home = Path(home)
        self.store = store or RemoteSessionStore(home)
        self.credentials = credentials or CredentialStore(self.home)
        self.timeout = aiohttp.ClientTimeout(total=timeout)

    def _auth(self, connection: Connection) -> dict[str, str]:
        try:
            credential = self.credentials.resolve(connection.credential_ref)
        except KeyError as error:
            raise RemoteSessionError(
                "credential_missing",
                "Configured credential reference is unavailable",
                503,
            ) from error
        if not credential.secret:
            raise RemoteSessionError(
                "credential_missing", "Configured credential has no value", 503
            )
        return {"Authorization": f"Bearer {credential.secret}"}

    async def _request(self, connection: Connection, path: str, payload: dict) -> dict:
        try:
            async with aiohttp.ClientSession(
                timeout=self.timeout, headers=self._auth(connection)
            ) as client:
                async with client.post(
                    urljoin(connection.base_url, path), json=payload
                ) as response:
                    text = await response.text()
                    if response.status in {401, 403}:
                        raise RemoteSessionError(
                            "unauthorized",
                            "Remote runtime rejected the configured credential",
                            401,
                        )
                    if response.status >= 400:
                        raise RemoteSessionError(
                            "unavailable",
                            f"Remote runtime returned HTTP {response.status}",
                            502,
                        )
                    try:
                        return json.loads(text) if text else {}
                    except ValueError as error:
                        raise RemoteSessionError(
                            "protocol_error", "Remote runtime returned invalid JSON"
                        ) from error
        except aiohttp.ClientError as error:
            raise RemoteSessionError(
                "unavailable", "Remote runtime is unavailable", 502
            ) from error

    async def _tool(
        self, connection: Connection, name: str, args: dict, session_id=""
    ) -> dict:
        body = await self._request(
            connection,
            "tools/invoke",
            {"tool": name, "args": args, "sessionKey": session_id or "main"},
        )
        if not body.get("ok"):
            raise RemoteSessionError("protocol_error", f"Remote tool failed: {name}")
        result = body.get("result", {})
        return result.get("details", result) if isinstance(result, dict) else {}

    async def sessions(self, connection_id: str) -> dict:
        connection = self.store.connection(connection_id)
        payload = await self._tool(connection, "sessions_list", {})
        rows = []
        for raw in payload.get("sessions", []):
            identifier = raw.get("key") or raw.get("id") or raw.get("sessionId")
            if identifier:
                rows.append(
                    {
                        "id": str(identifier),
                        "title": str(
                            raw.get("title") or raw.get("label") or identifier
                        ),
                        "status": raw.get("status") or raw.get("state"),
                        "last_message_at": raw.get("lastMessageAt")
                        or raw.get("updatedAt"),
                        "connection_id": connection.id,
                        "provenance": "remote",
                    }
                )
        return {
            "connection": connection.__dict__
            | {"credential_ref": connection.credential_ref},
            "sessions": self.store.retain(connection.id, rows),
        }

    async def history(self, connection_id: str, session_id: str, limit=50) -> dict:
        connection = self.store.connection(connection_id)
        payload = await self._tool(
            connection,
            "sessions_history",
            {
                "sessionKey": session_id,
                "limit": max(1, min(int(limit), 200)),
                "includeTools": False,
            },
            session_id,
        )
        rows = []
        for index, raw in enumerate(payload.get("messages", [])):
            rows.append(
                {
                    "id": str(raw.get("id") or f"remote-{index}"),
                    "role": str(raw.get("role") or raw.get("author") or "assistant"),
                    "content": _text(raw.get("content", raw)),
                    "created_at": raw.get("createdAt") or raw.get("timestamp"),
                    "source": {
                        "connection_id": connection_id,
                        "session_id": session_id,
                        "kind": "remote",
                    },
                }
            )
        return {
            "connection_id": connection_id,
            "session_id": session_id,
            "messages": rows,
        }

    async def stream(self, connection_id: str, session_id: str, payload: dict):
        connection = self.store.connection(connection_id)
        message = str(payload.get("message") or "").strip()
        attachments = payload.get("attachments", [])
        if not message and not attachments:
            raise ValueError("message or attachment required")
        if not isinstance(attachments, list) or len(attachments) > 8:
            raise ValueError("attachments must contain at most 8 items")
        input_rows = (
            [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": message}],
                }
            ]
            if message
            else []
        )
        for row in attachments:
            if (
                not isinstance(row, dict)
                or set(row) - {"filename", "media_type", "data"}
                or not row.get("data")
                or len(str(row["data"])) > 13_333_333
            ):
                raise ValueError("Invalid attachment")
            input_rows.append(
                {
                    "type": "input_file",
                    "source": {
                        "type": "base64",
                        "filename": str(row.get("filename") or "attachment"),
                        "media_type": str(
                            row.get("media_type") or "application/octet-stream"
                        ),
                        "data": row["data"],
                    },
                }
            )
        client = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=None), headers=self._auth(connection)
        )
        try:
            response = await client.post(
                urljoin(connection.base_url, "v1/responses"),
                headers={
                    "Accept": "text/event-stream",
                    "x-openclaw-session-key": session_id,
                },
                json={
                    "model": f"openclaw:{connection.agent_id}",
                    "user": session_id,
                    "stream": True,
                    "input": input_rows,
                },
            )
            if response.status in {401, 403}:
                response.release()
                raise RemoteSessionError(
                    "unauthorized",
                    "Remote runtime rejected the configured credential",
                    401,
                )
            if response.status >= 400:
                response.release()
                raise RemoteSessionError(
                    "unavailable", f"Remote runtime returned HTTP {response.status}"
                )
            async for chunk in response.content.iter_any():
                yield chunk
        except aiohttp.ClientError as error:
            raise RemoteSessionError(
                "unavailable", "Remote stream is unavailable"
            ) from error
        finally:
            await client.close()
