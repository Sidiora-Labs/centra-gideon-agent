from __future__ import annotations

import asyncio
import json
import re
import threading
from contextlib import closing
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit
from uuid import uuid4

import aiohttp

from gideon.core.config.credentials import get_credential

from .store import PeopleError, fields, instant, person_values, text

GRAPH_BASE = "https://graph.microsoft.com/v1.0/"
SOURCE_FIELDS = {"name", "owner_email", "credential_ref"}
MAX_PAGES = 40
MAX_MESSAGES = 1000
_SYNC_LOCK = threading.Lock()


class _PlainText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _plain(content, content_type):
    value = text(content or "", "message body", 200000)
    if content_type != "html":
        return value
    parser = _PlainText()
    parser.feed(value)
    return " ".join(" ".join(parser.parts).split())


def schema(db):
    db.execute(
        "CREATE TABLE IF NOT EXISTS teams_sources (id TEXT PRIMARY KEY, body TEXT NOT NULL, revision INTEGER NOT NULL)"
    )
    db.execute("""CREATE TABLE IF NOT EXISTS teams_messages
        (source_id TEXT NOT NULL, provenance_key TEXT NOT NULL, body TEXT NOT NULL,
         UNIQUE(source_id,provenance_key))""")


def _source_values(data):
    fields(data, SOURCE_FIELDS)
    name = text(data.get("name"), "name", 200, True)
    owner = person_values(
        {
            "name": name,
            "identities": [{"kind": "email", "value": data.get("owner_email")}],
        }
    )
    credential_ref = text(data.get("credential_ref"), "credential_ref", 120, True)
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", credential_ref):
        raise PeopleError("Microsoft Graph credential reference is invalid")
    return {
        "name": name,
        "owner_email": owner["identities"][0]["value"],
        "credential_ref": credential_ref,
    }


def sources(store):
    with closing(store.connect()) as db, db:
        schema(db)
        return [
            {**json.loads(body), "revision": revision}
            for body, revision in db.execute(
                "SELECT body,revision FROM teams_sources ORDER BY id"
            )
        ]


def get_source(store, source_id):
    found = next((row for row in sources(store) if row["id"] == source_id), None)
    if found is None:
        raise PeopleError("Teams source not found", 404)
    return found


def save_source(store, data, source_id=None):
    fields(data, SOURCE_FIELDS | ({"revision"} if source_id else set()))
    revision = data.get("revision")
    if source_id and (type(revision) is not int or revision < 1):
        raise PeopleError("Teams source revision is required")
    values = _source_values(
        {key: value for key, value in data.items() if key != "revision"}
    )
    source_id = source_id or uuid4().hex
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute(
            "SELECT body,revision FROM teams_sources WHERE id=?", (source_id,)
        ).fetchone()
        if revision is not None and old is None:
            raise PeopleError("Teams source not found", 404)
        if old and old[1] != revision:
            raise PeopleError("Teams source changed; reload", 409)
        if old and json.loads(old[0])["owner_email"] != values["owner_email"]:
            raise PeopleError(
                "Verified owner identity is immutable; create another source", 409
            )
        values.update(id=source_id, sync={"state": "not_synced", "coverage": "unknown"})
        next_revision = old[1] + 1 if old else 1
        db.execute(
            "INSERT INTO teams_sources VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body,revision=excluded.revision",
            (source_id, json.dumps(values), next_revision),
        )
    return {**values, "revision": next_revision}


def messages(store, source_id):
    get_source(store, source_id)
    with closing(store.connect()) as db:
        return [
            json.loads(body)
            for body, in db.execute(
                "SELECT body FROM teams_messages WHERE source_id=? ORDER BY provenance_key",
                (source_id,),
            )
        ]


class GraphClient:
    def __init__(self, token, base_url=GRAPH_BASE, session=None):
        self.token = token
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        parsed = urlsplit(self.base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise PeopleError("Invalid Microsoft Graph endpoint")
        self._origin = (parsed.scheme, parsed.netloc)
        self.session = session

    def _url(self, path):
        url = (
            path
            if path.startswith(("http://", "https://"))
            else urljoin(self.base_url, path.lstrip("/"))
        )
        parsed = urlsplit(url)
        if (parsed.scheme, parsed.netloc) != self._origin:
            raise PeopleError("Microsoft Graph continuation changed origin", 503)
        return url

    async def get(self, path):
        own = self.session is None
        session = self.session or aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30)
        )
        try:
            async with session.get(
                self._url(path),
                headers={
                    "Authorization": "Bearer " + self.token,
                    "Accept": "application/json",
                },
            ) as response:
                if response.status in (401, 403):
                    raise PeopleError(
                        "Microsoft Graph connection was revoked or lacks required Teams history access",
                        401,
                    )
                if response.status != 200:
                    raise PeopleError(
                        f"Microsoft Graph request failed with HTTP {response.status}",
                        503,
                    )
                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, json.JSONDecodeError):
                    raise PeopleError(
                        "Microsoft Graph returned invalid JSON", 503
                    ) from None
                if not isinstance(payload, dict):
                    raise PeopleError(
                        "Microsoft Graph returned an invalid document", 503
                    )
                return payload
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise PeopleError("Microsoft Graph could not be reached", 503) from None
        finally:
            if own:
                await session.close()

    async def pages(self, path):
        current = path
        for _ in range(MAX_PAGES):
            page = await self.get(current)
            value = page.get("value")
            if not isinstance(value, list):
                raise PeopleError("Microsoft Graph page has no value list", 503)
            yield value
            current = page.get("@odata.nextLink")
            if current is None:
                return
            if not isinstance(current, str):
                raise PeopleError("Microsoft Graph continuation is invalid", 503)
        raise PeopleError(
            "Microsoft Graph pagination exceeded the bounded page limit", 503
        )


async def _collect_pages(client, path, limit=MAX_MESSAGES):
    rows = []
    async for page in client.pages(path):
        if len(rows) + len(page) > limit:
            return rows + page[: max(0, limit - len(rows))], False
        rows.extend(page)
    return rows, True


def _identity(message):
    sender = message.get("from") or {}
    user = sender.get("user") if isinstance(sender, dict) else None
    if not isinstance(user, dict):
        user = {}
    return {
        "id": text(user.get("id", ""), "sender id", 500),
        "name": text(user.get("displayName", ""), "sender name", 500),
    }


def _person_id(people, sender):
    keys = []
    if sender["id"]:
        keys.append({"kind": "handle", "value": "microsoft:" + sender["id"]})
    matches = [
        row for row in people if any(identity in row["identities"] for identity in keys)
    ]
    return matches[0]["id"] if len(matches) == 1 else None


def _normalize(
    message,
    *,
    source_kind,
    conversation_id,
    owner_id,
    people,
    team_id=None,
    channel_id=None,
    reply_to=None,
):
    if not isinstance(message, dict):
        raise PeopleError("Microsoft Graph message is invalid", 503)
    message_id = text(message.get("id"), "message id", 500, True)
    created = instant(message.get("createdDateTime"))
    sender = _identity(message)
    body = message.get("body") or {}
    if not isinstance(body, dict):
        body = {}
    attachments = message.get("attachments") or []
    if not isinstance(attachments, list):
        raise PeopleError("Microsoft Graph attachments are invalid", 503)
    provenance_key = ":".join(
        (source_kind, conversation_id, reply_to or "", message_id)
    )
    return {
        "provenance_key": provenance_key,
        "provider": "microsoft_graph",
        "source_kind": source_kind,
        "conversation_id": conversation_id,
        "team_id": team_id,
        "channel_id": channel_id,
        "message_id": message_id,
        "reply_to_id": reply_to or message.get("replyToId"),
        "sender": sender,
        "person_id": _person_id(people, sender),
        "direction": "outbound" if sender["id"] == owner_id else "inbound",
        "created_at": created,
        "modified_at": message.get("lastModifiedDateTime"),
        "deleted_at": message.get("deletedDateTime"),
        "etag": text(message.get("etag", ""), "etag", 500),
        "body": _plain(body.get("content", ""), body.get("contentType"))[:100000],
        "attachments": [
            {
                "id": text(item.get("id", ""), "attachment id", 500),
                "name": text(item.get("name", ""), "attachment name", 1000),
                "content_type": text(
                    item.get("contentType", ""), "attachment type", 500
                ),
            }
            for item in attachments
            if isinstance(item, dict)
        ][:100],
    }


async def _history(client, owner, people):
    discovered_complete = True
    chats, complete = await _collect_pages(client, "me/chats?$top=50")
    discovered_complete &= complete
    teams, complete = await _collect_pages(
        client, "me/joinedTeams?$select=id,displayName"
    )
    discovered_complete &= complete
    conversations = [
        ("chat", text(row.get("id"), "chat id", 500, True), None, None)
        for row in chats
        if isinstance(row, dict)
    ]
    for team in teams:
        if not isinstance(team, dict):
            continue
        team_id = text(team.get("id"), "team id", 500, True)
        channels, complete = await _collect_pages(
            client, f'teams/{quote(team_id, safe="")}/channels?$top=50'
        )
        discovered_complete &= complete
        conversations.extend(
            (
                "channel",
                text(row.get("id"), "channel id", 500, True),
                team_id,
                text(row.get("id"), "channel id", 500, True),
            )
            for row in channels
            if isinstance(row, dict)
        )
    normalized = {}
    for kind, conversation_id, team_id, channel_id in conversations:
        if len(normalized) >= MAX_MESSAGES:
            discovered_complete = False
            break
        if kind == "chat":
            path = f'chats/{quote(conversation_id, safe="")}/messages?$top=50'
        else:
            path = f'teams/{quote(team_id, safe="")}/channels/{quote(channel_id, safe="")}/messages?$top=50&$expand=replies'
        rows, complete = await _collect_pages(
            client, path, MAX_MESSAGES - len(normalized)
        )
        discovered_complete &= complete
        for raw in rows:
            row = _normalize(
                raw,
                source_kind=kind,
                conversation_id=conversation_id,
                owner_id=owner["id"],
                people=people,
                team_id=team_id,
                channel_id=channel_id,
            )
            old = normalized.get(row["provenance_key"])
            if old is not None and old != row:
                raise PeopleError(
                    "Microsoft Graph returned conflicting message identities", 409
                )
            normalized[row["provenance_key"]] = row
            for reply in raw.get("replies") or []:
                nested = _normalize(
                    reply,
                    source_kind=kind,
                    conversation_id=conversation_id,
                    owner_id=owner["id"],
                    people=people,
                    team_id=team_id,
                    channel_id=channel_id,
                    reply_to=row["message_id"],
                )
                normalized[nested["provenance_key"]] = nested
    return normalized, discovered_complete, len(conversations)


async def sync(store, source_id, client=None):
    if not _SYNC_LOCK.acquire(blocking=False):
        raise PeopleError(
            "A Teams sync is already running; retry after it completes", 409
        )
    source = get_source(store, source_id)
    try:
        token = (
            get_credential(source["credential_ref"]) if client is None else client.token
        )
        if not token:
            raise PeopleError(
                "Microsoft Graph credential is unavailable; connect this source first",
                503,
            )
        client = client or GraphClient(token)
        profile = await client.get("me?$select=id,mail,userPrincipalName,displayName")
        owner_id = text(profile.get("id"), "Microsoft account id", 500, True)
        email = str(
            profile.get("mail") or profile.get("userPrincipalName") or ""
        ).casefold()
        if email != source["owner_email"]:
            raise PeopleError(
                "Microsoft Graph signed-in identity does not match the configured owner",
                409,
            )
        captured = datetime.now(timezone.utc).isoformat()
        normalized, complete, conversation_count = await _history(
            client, {"id": owner_id}, store.people()
        )
        with closing(store.connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute(
                "SELECT revision FROM teams_sources WHERE id=?", (source_id,)
            ).fetchone()
            if current is None or current[0] != source["revision"]:
                raise PeopleError("Teams source changed during sync; retry", 409)
            for row in normalized.values():
                db.execute(
                    "INSERT INTO teams_messages VALUES (?,?,?) ON CONFLICT(source_id,provenance_key) DO UPDATE SET body=excluded.body",
                    (source_id, row["provenance_key"], json.dumps(row)),
                )
            result = {
                "state": "synced",
                "coverage": (
                    "complete_discovered_history"
                    if complete
                    else "bounded_partial_history"
                ),
                "captured_at": captured,
                "messages_seen": len(normalized),
                "conversations_seen": conversation_count,
                "owner_graph_id": owner_id,
                "next_action": (
                    None
                    if complete
                    else "Narrow source scope or resume a later bounded sync"
                ),
            }
            body = {key: value for key, value in source.items() if key != "revision"}
            body["sync"] = result
            db.execute(
                "UPDATE teams_sources SET body=? WHERE id=? AND revision=?",
                (json.dumps(body), source_id, source["revision"]),
            )
        return result
    except PeopleError as exc:
        state = "revoked" if exc.status == 401 else "failed"
        with closing(store.connect()) as db, db:
            schema(db)
            body = {key: value for key, value in source.items() if key != "revision"}
            body["sync"] = {"state": state, "coverage": "unknown", "error": str(exc)}
            db.execute(
                "UPDATE teams_sources SET body=? WHERE id=? AND revision=?",
                (json.dumps(body), source_id, source["revision"]),
            )
        raise
    finally:
        _SYNC_LOCK.release()
