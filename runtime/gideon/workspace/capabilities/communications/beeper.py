from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import quote, urlencode, urlsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout

from gideon.core.config.credentials import get_credential

from .store import PeopleError, fields, text


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS beeper_settings (id INTEGER PRIMARY KEY,body TEXT NOT NULL,revision INTEGER NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS beeper_pages (key TEXT PRIMARY KEY,body TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS beeper_assets (id TEXT PRIMARY KEY,body BLOB NOT NULL,digest TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS beeper_outbox (id TEXT PRIMARY KEY,request_key TEXT UNIQUE NOT NULL,body TEXT NOT NULL,revision INTEGER NOT NULL)"
    )


def settings(store):
    with closing(store.connect()) as db, db:
        schema(db)
        row = db.execute(
            "SELECT body,revision FROM beeper_settings WHERE id=1"
        ).fetchone()
        body, revision = (
            (json.loads(row[0]), row[1])
            if row
            else ({"base_url": "http://127.0.0.1:23373", "credential_ref": ""}, 0)
        )
        return {
            **body,
            "revision": revision,
            "connected": bool(body["credential_ref"]),
            "transport_mode": "manual_refresh_only",
        }


def endpoint(value):
    parsed = urlsplit(text(value, "base_url", 500, True))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in ("", "/")
    ):
        raise PeopleError("Beeper Desktop endpoint must be a loopback HTTP origin")
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError()
    except ValueError:
        raise PeopleError("Invalid Beeper port") from None
    return value.rstrip("/")


def configure(store, data):
    fields(
        data, {"base_url", "credential_ref", "revision", "connected", "transport_mode"}
    )
    url = endpoint(data.get("base_url"))
    key = text(data.get("credential_ref"), "credential_ref", 120, True)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
        raise PeopleError("Invalid credential reference")
    revision = data.get("revision")
    if type(revision) is not int or revision < 0:
        raise PeopleError("Current settings revision is required")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute("SELECT revision FROM beeper_settings WHERE id=1").fetchone()
        if (old[0] if old else 0) != revision:
            raise PeopleError("Beeper settings changed; reload", 409)
        body = {"base_url": url, "credential_ref": key}
        db.execute(
            "INSERT INTO beeper_settings VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
            (json.dumps(body), revision + 1),
        )
        db.execute("DELETE FROM beeper_pages")
        db.execute("DELETE FROM beeper_assets")
    return {
        **body,
        "revision": revision + 1,
        "connected": True,
        "transport_mode": "manual_refresh_only",
    }


def disconnect(store, data):
    fields(data, {"revision"})
    revision = data.get("revision")
    if type(revision) is not int or revision < 0:
        raise PeopleError("Current settings revision is required")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute(
            "SELECT body,revision FROM beeper_settings WHERE id=1"
        ).fetchone()
        current = old[1] if old else 0
        if current != revision:
            raise PeopleError("Beeper settings changed; reload", 409)
        base_url = json.loads(old[0])["base_url"] if old else "http://127.0.0.1:23373"
        body = {"base_url": base_url, "credential_ref": ""}
        db.execute(
            "INSERT INTO beeper_settings VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
            (json.dumps(body), revision + 1),
        )
        db.execute("DELETE FROM beeper_pages")
        db.execute("DELETE FROM beeper_assets")
    return {
        **body,
        "revision": revision + 1,
        "connected": False,
        "transport_mode": "manual_refresh_only",
    }


class Client:
    def __init__(self, config):
        self.base = endpoint(config["base_url"])
        self.token = (
            get_credential(config["credential_ref"])
            if config["credential_ref"]
            else None
        )
        if not self.token:
            raise PeopleError("Beeper connection credential is unavailable", 503)

    async def request(self, method, path, data=None, binary=False):
        try:
            async with ClientSession(
                timeout=ClientTimeout(total=20),
                headers={"Authorization": "Bearer " + self.token},
            ) as session:
                async with session.request(
                    method, self.base + path, json=data, allow_redirects=False
                ) as response:
                    limit = 8 * 1024 * 1024 if binary else 2 * 1024 * 1024
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > limit:
                            raise PeopleError(
                                "Beeper response exceeded its size limit", 502
                            )
                        chunks.append(chunk)
                    if not 200 <= response.status < 300:
                        raise PeopleError(
                            "Beeper request failed with status " + str(response.status),
                            502,
                        )
                    raw = b"".join(chunks)
                    return raw if binary else json.loads(raw)
        except (ClientError, asyncio.TimeoutError, ValueError):
            raise PeopleError(
                "Beeper connection failed or returned invalid data", 503
            ) from None


def stored_page(store, chat_id=None):
    key = "messages:" + chat_id if chat_id else "chats"
    with closing(store.connect()) as db, db:
        schema(db)
        row = db.execute("SELECT body FROM beeper_pages WHERE key=?", (key,)).fetchone()
        return (
            json.loads(row[0])
            if row
            else {"items": [], "coverage": "unknown", "observed_at": None}
        )


def persist_page(
    store, page, chat_id=None, provenance="beeper_api", expected_revision=None
):
    if (
        not isinstance(page, dict)
        or not isinstance(page.get("items"), list)
        or len(page["items"]) > 500
    ):
        raise PeopleError("Beeper page must contain at most 500 items")
    if len(json.dumps(page).encode()) > 2 * 1024 * 1024:
        raise PeopleError("Beeper page exceeds 2 MiB")
    seen = set()
    for row in page["items"]:
        if not isinstance(row, dict):
            raise PeopleError("Invalid Beeper page item")
        identity = text(row.get("id"), "Beeper identity", 500, True)
        if identity in seen:
            raise PeopleError("Duplicate Beeper page identity")
        seen.add(identity)
        if chat_id and row.get("chatID") != chat_id:
            raise PeopleError("Beeper message belongs to another chat")
        if chat_id and not isinstance(row.get("attachments", []), list):
            raise PeopleError("Invalid Beeper attachment list")
    body = {
        **page,
        "coverage": "available_page_only",
        "provenance": provenance,
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }
    key = "messages:" + chat_id if chat_id else "chats"
    with closing(store.connect()) as db, db:
        schema(db)
        if expected_revision is not None:
            current = db.execute(
                "SELECT revision FROM beeper_settings WHERE id=1"
            ).fetchone()
            if not current or current[0] != expected_revision:
                raise PeopleError("Beeper connection changed during fetch", 409)
        db.execute(
            "INSERT INTO beeper_pages VALUES (?,?) ON CONFLICT(key) DO UPDATE SET body=excluded.body",
            (key, json.dumps(body)),
        )
    return body


async def refresh(store, chat_id=None, cursor=None):
    if chat_id is not None:
        chat_id = text(chat_id, "chat_id", 500, True)
    config = settings(store)
    client = Client(config)
    path = (
        "/v1/chats/" + quote(chat_id, safe="") + "/messages" if chat_id else "/v1/chats"
    )
    if cursor:
        path += "?" + urlencode({"cursor": text(cursor, "cursor", 1000, True)})
    return persist_page(
        store,
        await client.request("GET", path),
        chat_id,
        expected_revision=config["revision"],
    )


async def fetch_asset(store, chat_id, asset_id):
    chat_id = text(chat_id, "chat_id", 500, True)
    asset_id = text(asset_id, "asset_id", 1000, True)
    attachments = [
        item
        for message in stored_page(store, chat_id)["items"]
        for item in message.get("attachments", [])
    ]
    if not any(
        isinstance(item, dict) and item.get("id") == asset_id for item in attachments
    ):
        raise PeopleError("Attachment is not present in the mirrored chat", 404)
    if not asset_id.startswith(("mxc://", "localmxc://")):
        raise PeopleError("Only Beeper media identities can be fetched")
    config = settings(store)
    raw = await Client(config).request(
        "GET", "/v1/assets/serve?" + urlencode({"url": asset_id}), binary=True
    )
    digest = hashlib.sha256(raw).hexdigest()
    with closing(store.connect()) as db, db:
        schema(db)
        current = db.execute(
            "SELECT revision FROM beeper_settings WHERE id=1"
        ).fetchone()
        if not current or current[0] != config["revision"]:
            raise PeopleError("Beeper connection changed during attachment fetch", 409)
        db.execute(
            "INSERT INTO beeper_assets VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,digest=excluded.digest",
            (asset_id, raw, digest),
        )
    return {"id": asset_id, "bytes": len(raw), "digest": digest}


def asset(store, asset_id):
    with closing(store.connect()) as db, db:
        schema(db)
        row = db.execute(
            "SELECT body,digest FROM beeper_assets WHERE id=?", (asset_id,)
        ).fetchone()
        if not row:
            raise PeopleError("Attachment has not been fetched", 404)
        return {
            "id": asset_id,
            "content_base64": base64.b64encode(row[0]).decode(),
            "digest": row[1],
            "bytes": len(row[0]),
        }


def outbox(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [
            {**json.loads(row[0]), "revision": row[1]}
            for row in db.execute(
                "SELECT body,revision FROM beeper_outbox ORDER BY rowid DESC"
            )
        ]


def get_outbox(store, outbox_id):
    found = next((row for row in outbox(store) if row["id"] == outbox_id), None)
    if not found:
        raise PeopleError("Outbox item not found", 404)
    return found


def draft(store, data):
    fields(data, {"request_key", "chat_id", "text"})
    request = {
        key: text(data.get(key), key, 10000 if key == "text" else 500, True)
        for key in ("request_key", "chat_id", "text")
    }
    connection_revision = settings(store)["revision"]
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        previous = db.execute(
            "SELECT body,revision FROM beeper_outbox WHERE request_key=?",
            (request["request_key"],),
        ).fetchone()
        if previous:
            body = json.loads(previous[0])
            if any(body[key] != value for key, value in request.items()):
                raise PeopleError("Outbox request key has conflicting content", 409)
            return {**body, "revision": previous[1]}, False
        row = {
            **request,
            "connection_revision": connection_revision,
            "id": uuid4().hex,
            "state": "draft",
            "pending_message_id": None,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "delivery": "not_sent",
        }
        db.execute(
            "INSERT INTO beeper_outbox VALUES (?,?,?,1)",
            (row["id"], request["request_key"], json.dumps(row)),
        )
    return {**row, "revision": 1}, True


def transition(store, outbox_id, revision, allowed, state, **changes):
    if type(revision) is not int:
        raise PeopleError("Current outbox revision is required")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute(
            "SELECT body,revision FROM beeper_outbox WHERE id=?", (outbox_id,)
        ).fetchone()
        if not old:
            raise PeopleError("Outbox item not found", 404)
        row = json.loads(old[0])
        if old[1] != revision or row["state"] not in allowed:
            raise PeopleError("Outbox state changed or action is not permitted", 409)
        row.update(changes, state=state)
        db.execute(
            "UPDATE beeper_outbox SET body=?,revision=? WHERE id=?",
            (json.dumps(row), revision + 1, outbox_id),
        )
    return {**row, "revision": revision + 1}


async def send(store, outbox_id, data):
    fields(data, {"revision", "confirm_send"})
    if data.get("confirm_send") is not True:
        raise PeopleError("Explicit send confirmation is required")
    current = settings(store)
    draft_row = get_outbox(store, outbox_id)
    if draft_row["connection_revision"] != current["revision"]:
        raise PeopleError("Beeper connection changed; create a new reviewed draft", 409)
    client = Client(current)
    row = transition(
        store,
        outbox_id,
        data.get("revision"),
        {"draft"},
        "sending",
        delivery="unknown",
        started_at=datetime.now(timezone.utc).isoformat(),
    )
    try:
        response = await client.request(
            "POST",
            "/v1/chats/" + quote(row["chat_id"], safe="") + "/messages",
            {"text": row["text"]},
        )
        pending = text(
            response.get("pendingMessageID"), "pending message ID", 500, True
        )
        if response.get("chatID") != row["chat_id"]:
            raise PeopleError("Beeper acknowledgement belongs to another chat", 502)
        return transition(
            store,
            outbox_id,
            row["revision"],
            {"sending"},
            "pending",
            pending_message_id=pending,
            delivery="not_confirmed",
        )
    except (PeopleError, AttributeError):
        return transition(
            store,
            outbox_id,
            row["revision"],
            {"sending"},
            "unknown",
            delivery="unknown",
            error="Send outcome is unknown. Do not resend automatically.",
        )


async def reconcile(store, outbox_id, revision):
    row = get_outbox(store, outbox_id)
    if row["revision"] != revision:
        raise PeopleError("Outbox changed; reload", 409)
    if not row["pending_message_id"] or row["state"] not in ("pending", "unknown"):
        raise PeopleError(
            "No pending message identity is available for reconciliation", 409
        )
    config = settings(store)
    if row["connection_revision"] != config["revision"]:
        raise PeopleError(
            "Beeper connection changed; reconciliation requires the original connection",
            409,
        )
    response = await Client(config).request(
        "GET",
        "/v1/chats/"
        + quote(row["chat_id"], safe="")
        + "/messages/"
        + quote(row["pending_message_id"], safe=""),
    )
    if not isinstance(response, dict) or response.get("chatID") != row["chat_id"]:
        raise PeopleError("Beeper reconciliation returned another chat", 502)
    send_status = response.get("sendStatus")
    status = send_status.get("status") if isinstance(send_status, dict) else None
    if not response.get("id"):
        status = None
    state = {
        "SUCCESS": "confirmed",
        "PENDING": "pending",
        "FAIL_RETRIABLE": "failed",
        "FAIL_PERMANENT": "failed",
    }.get(status, "unknown")
    return transition(
        store,
        outbox_id,
        revision,
        {"pending", "unknown"},
        state,
        delivery="bridge_confirmed" if state == "confirmed" else "not_confirmed",
        remote_message_id=response.get("id"),
    )


def discard(store, outbox_id, revision):
    return transition(
        store,
        outbox_id,
        revision,
        {"draft", "pending", "unknown", "failed"},
        "discarded",
        delivery="not_retracted",
    )


def recover(store, outbox_id, revision):
    row = get_outbox(store, outbox_id)
    if (
        row["state"] != "sending"
        or (
            datetime.now(timezone.utc) - datetime.fromisoformat(row["started_at"])
        ).total_seconds()
        < 60
    ):
        raise PeopleError(
            "Only an interrupted send older than one minute can be marked unknown", 409
        )
    return transition(
        store,
        outbox_id,
        revision,
        {"sending"},
        "unknown",
        delivery="unknown",
        error="Interrupted send; confirm in Beeper before creating any replacement.",
    )
