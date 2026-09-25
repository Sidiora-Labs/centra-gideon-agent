from __future__ import annotations

import asyncio
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout

from gideon.core.config.credentials import get_credential

from . import social
from .store import PeopleError, fields, text


def schema(db):
    social.schema(db)
    db.execute(
        "CREATE TABLE IF NOT EXISTS x_snapshots(account_id TEXT PRIMARY KEY,body TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS x_drafts(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,request_key TEXT NOT NULL,original TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(account_id,request_key))"
    )


def account(store, account_id):
    row = social.get(store, account_id)
    if (
        row["platform"] != "x"
        or row["status"] != "active"
        or not re.fullmatch(r"[a-z0-9_]{1,15}", row["handle"])
    ):
        raise PeopleError("An active X registration with a valid handle is required")
    return row


def normalize_page(user, page):
    if (
        not isinstance(user, dict)
        or not isinstance(page, dict)
        or not isinstance(user.get("data"), dict)
    ):
        raise PeopleError("Malformed X response", 502)
    profile = user["data"]
    if (
        not isinstance(profile.get("id"), str)
        or not profile["id"].isdigit()
        or not isinstance(profile.get("username"), str)
    ):
        raise PeopleError("Malformed X user identity", 502)
    rows = page.get("data", [])
    meta = page.get("meta", {})
    if not isinstance(rows, list) or len(rows) > 100 or not isinstance(meta, dict):
        raise PeopleError("Malformed X page", 502)
    token = meta.get("next_token")
    if token is not None and (not isinstance(token, str) or len(token) > 1000):
        raise PeopleError("Malformed X pagination token", 502)
    posts = []
    for row in rows:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("id"), str)
            or not row["id"].isdigit()
            or not isinstance(row.get("text"), str)
        ):
            raise PeopleError("Malformed X post", 502)
        posts.append(
            {
                "id": row["id"],
                "text": text(row["text"], "post text", 30000),
                "created_at": row.get("created_at"),
                "url": "https://x.com/i/status/" + row["id"],
            }
        )
    if len({row["id"] for row in posts}) != len(posts):
        raise PeopleError("Duplicate X post identities", 502)
    return {
        "user_id": profile["id"],
        "username": profile["username"],
        "posts": posts,
        "next_token": token,
        "coverage": (
            "partial"
            if user.get("errors") or page.get("errors")
            else "available_page_only"
        ),
    }


async def remote(row, token=None, *, _api_root="https://api.x.com/2/"):
    credential = (
        get_credential(row["credential_ref"]) if row["credential_ref"] else None
    )
    if not credential:
        raise PeopleError("X credential is unavailable", 409)
    async with ClientSession(
        timeout=ClientTimeout(total=20),
        headers={"Authorization": "Bearer " + credential},
    ) as client:

        async def read(path, params=None):
            async with client.get(
                _api_root + path, params=params, allow_redirects=False
            ) as response:
                if response.status != 200:
                    raise PeopleError(f"X read failed (HTTP {response.status})", 502)
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 2 * 1024 * 1024:
                        raise PeopleError("X response exceeds limit", 502)
                return json.loads(data)

        user = await read("users/by/username/" + row["handle"])
        if (
            not isinstance(user, dict)
            or not isinstance(user.get("data"), dict)
            or not re.fullmatch(r"[0-9]+", str(user["data"].get("id", "")))
        ):
            raise PeopleError("X user lookup failed", 502)
        params = {"max_results": "100", "tweet.fields": "created_at,author_id"}
        if token:
            params["pagination_token"] = token
        page = await read("users/" + user["data"]["id"] + "/tweets", params)
        result = normalize_page(user, page)
        if result["username"].casefold() != row["handle"]:
            raise PeopleError("X account identity changed during lookup", 409)
        return result


def snapshot(store, account_id):
    current = account(store, account_id)
    with closing(store.connect()) as db, db:
        schema(db)
        row = db.execute(
            "SELECT body FROM x_snapshots WHERE account_id=?", (account_id,)
        ).fetchone()
    result = (
        json.loads(row[0])
        if row
        else {"state": "not_synced", "coverage": "unknown", "posts": []}
    )
    if result.get("account_revision", current["revision"]) != current["revision"]:
        return {"state": "registration_changed", "coverage": "unknown", "posts": []}
    return result


async def sync(store, account_id, data):
    fields(data, {"pagination_token"})
    token = text(data.get("pagination_token", ""), "pagination_token", 1000)
    row = account(store, account_id)
    try:
        result = {**await remote(row, token or None), "state": "synced"}
    except (
        PeopleError,
        ClientError,
        asyncio.TimeoutError,
        ValueError,
        TypeError,
    ) as error:
        result = {
            "state": "failed",
            "coverage": "unknown",
            "posts": [],
            "error": str(error) if isinstance(error, PeopleError) else "X read failed",
        }
    result.update(
        account_id=account_id,
        account_revision=row["revision"],
        captured_at=datetime.now(timezone.utc).isoformat(),
        requested_token=token,
    )
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        current = db.execute(
            "SELECT revision FROM social_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not current or current[0] != row["revision"]:
            raise PeopleError("X registration changed; reload", 409)
        db.execute(
            "INSERT OR REPLACE INTO x_snapshots VALUES (?,?)",
            (account_id, json.dumps(result)),
        )
    return result


def drafts(store, account_id):
    current = social.get(store, account_id)
    with closing(store.connect()) as db, db:
        schema(db)
        rows = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM x_drafts WHERE account_id=? ORDER BY rowid",
                (account_id,),
            )
        ]
    for row in rows:
        if (
            row["account_revision"] != current["revision"]
            or current["status"] != "active"
            or current["platform"] != "x"
        ):
            row.pop("handoff_url", None)
            row["state"] = "registration_changed"
    return rows


def save_draft(store, account_id, data, draft_id=None):
    fields(data, {"text", "revision"} if draft_id else {"text", "request_key"})
    current = account(store, account_id)
    content = text(data.get("text"), "text", 280, True)
    key = (
        text(data.get("request_key"), "request_key", 200, True)
        if not draft_id
        else None
    )
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        latest = db.execute(
            "SELECT revision FROM social_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not latest or latest[0] != current["revision"]:
            raise PeopleError("X registration changed; reload", 409)
        if key:
            previous = db.execute(
                "SELECT original,body FROM x_drafts WHERE account_id=? AND request_key=?",
                (account_id, key),
            ).fetchone()
            if previous:
                if previous[0] != content:
                    raise PeopleError("X draft request conflicts", 409)
                return json.loads(previous[1])
        old = (
            db.execute(
                "SELECT body FROM x_drafts WHERE id=? AND account_id=?",
                (draft_id, account_id),
            ).fetchone()
            if draft_id
            else None
        )
        if draft_id and not old:
            raise PeopleError("X draft not found", 404)
        prior = json.loads(old[0]) if old else None
        if prior and (
            type(data.get("revision")) is not int
            or data["revision"] != prior["revision"]
        ):
            raise PeopleError("X draft changed; reload", 409)
        row = {
            "id": draft_id or uuid4().hex,
            "account_id": account_id,
            "account_revision": current["revision"],
            "handle": current["handle"],
            "text": content,
            "revision": prior["revision"] + 1 if prior else 1,
            "state": "draft",
            "external_posted": False,
            "created_at": (
                prior["created_at"] if prior else datetime.now(timezone.utc).isoformat()
            ),
        }
        if prior:
            db.execute(
                "UPDATE x_drafts SET body=? WHERE id=?", (json.dumps(row), row["id"])
            )
        else:
            db.execute(
                "INSERT INTO x_drafts VALUES (?,?,?,?,?)",
                (row["id"], account_id, key, content, json.dumps(row)),
            )
        return row


def review(store, account_id, draft_id, data):
    fields(data, {"revision", "confirm_review"})
    if data.get("confirm_review") is not True or type(data.get("revision")) is not int:
        raise PeopleError(
            "Explicit review confirmation and current revision are required"
        )
    current = account(store, account_id)
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        old = db.execute(
            "SELECT body FROM x_drafts WHERE id=? AND account_id=?",
            (draft_id, account_id),
        ).fetchone()
        if not old:
            raise PeopleError("X draft not found", 404)
        row = json.loads(old[0])
        latest = db.execute(
            "SELECT revision FROM social_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if (
            row["revision"] != data["revision"]
            or row["account_revision"] != current["revision"]
            or not latest
            or latest[0] != current["revision"]
        ):
            raise PeopleError("X draft or registration changed; review again", 409)
        row.update(
            state="reviewed_handoff",
            reviewed_at=datetime.now(timezone.utc).isoformat(),
            handoff_url="https://twitter.com/intent/tweet?"
            + urlencode({"text": row["text"]}),
            browser_account_warning="Verify the signed-in X account before posting; the browser identity is not controlled by this registration.",
        )
        db.execute("UPDATE x_drafts SET body=? WHERE id=?", (json.dumps(row), draft_id))
    return row
