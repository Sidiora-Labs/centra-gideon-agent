from __future__ import annotations

import json
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from gideon.core.config.loader import AppConfig

from . import social
from .store import PeopleError, fields, text


def agents():
    return [
        {"id": name, "provider": profile.provider or "inherited"}
        for name, profile in sorted(AppConfig.load().agents.items())
    ]


def schema(db):
    social.schema(db)
    db.execute(
        "CREATE TABLE IF NOT EXISTS platform_assignments(id TEXT PRIMARY KEY,account_id TEXT NOT NULL,agent_id TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,body TEXT NOT NULL)"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS platform_active_owner ON platform_assignments(account_id) WHERE state='active'"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS platform_assignment_requests(key TEXT PRIMARY KEY,body TEXT NOT NULL,assignment_id TEXT NOT NULL)"
    )
    db.execute(
        "CREATE TABLE IF NOT EXISTS platform_assignment_history(id INTEGER PRIMARY KEY,assignment_id TEXT NOT NULL,body TEXT NOT NULL)"
    )


def records(store):
    profiles = {row["id"] for row in agents()}
    accounts = {row["id"]: row for row in social.accounts(store)}
    with closing(store.connect()) as db, db:
        schema(db)
        rows = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM platform_assignments ORDER BY rowid"
            )
        ]
    for row in rows:
        account = accounts.get(row["account_id"])
        row["agent_available"] = row["agent_id"] in profiles
        row["account_available"] = account is not None
        row["account_changed"] = (
            account is None
            or account["revision"] != row["account_revision"]
            or account["status"] != "active"
        )
        row["usable"] = (
            row["state"] == "active"
            and row["agent_available"]
            and not row["account_changed"]
        )
    return rows


def get(store, assignment_id):
    row = next((row for row in records(store) if row["id"] == assignment_id), None)
    if not row:
        raise PeopleError("Platform assignment not found", 404)
    return row


def event(db, row, reason):
    body = {**row, "reason": reason, "at": datetime.now(timezone.utc).isoformat()}
    db.execute(
        "INSERT INTO platform_assignment_history(assignment_id,body) VALUES (?,?)",
        (row["id"], json.dumps(body)),
    )


def create(store, data):
    fields(
        data, {"account_id", "account_revision", "agent_id", "reason", "request_key"}
    )
    account_id = text(data.get("account_id"), "account_id", 100, True)
    agent_id = text(data.get("agent_id"), "agent_id", 200, True)
    reason = text(data.get("reason"), "reason", 2000, True)
    request_key = text(data.get("request_key"), "request_key", 200, True)
    revision = data.get("account_revision")
    if type(revision) is not int or revision < 1:
        raise PeopleError("Current account revision is required")
    original = json.dumps(
        {
            "account_id": account_id,
            "account_revision": revision,
            "agent_id": agent_id,
            "reason": reason,
        },
        sort_keys=True,
    )
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        previous = db.execute(
            "SELECT body,assignment_id FROM platform_assignment_requests WHERE key=?",
            (request_key,),
        ).fetchone()
        if previous:
            if previous[0] != original:
                raise PeopleError("Assignment request key conflicts", 409)
            return json.loads(
                db.execute(
                    "SELECT body FROM platform_assignments WHERE id=?", (previous[1],)
                ).fetchone()[0]
            )
        if agent_id not in {row["id"] for row in agents()}:
            raise PeopleError("Configured agent not found", 404)
        account = db.execute(
            "SELECT body,revision FROM social_accounts WHERE id=?", (account_id,)
        ).fetchone()
        if not account:
            raise PeopleError("Registered platform account not found", 404)
        if account[1] != revision or json.loads(account[0])["status"] != "active":
            raise PeopleError("Registered platform account changed or inactive", 409)
        row = {
            "id": uuid4().hex,
            "account_id": account_id,
            "account_revision": revision,
            "agent_id": agent_id,
            "state": "requested",
            "revision": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "qualification": "local_assignment_only",
            "external_account_changed": False,
        }
        db.execute(
            "INSERT INTO platform_assignments VALUES (?,?,?,?,?,?)",
            (row["id"], account_id, agent_id, row["state"], 1, json.dumps(row)),
        )
        db.execute(
            "INSERT INTO platform_assignment_requests VALUES (?,?,?)",
            (request_key, original, row["id"]),
        )
        event(db, row, reason)
    return row


def change(store, assignment_id, data):
    fields(data, {"revision", "state", "account_revision", "reason"})
    revision = data.get("revision")
    target = data.get("state")
    account_revision = data.get("account_revision")
    reason = text(data.get("reason"), "reason", 2000, True)
    if (
        type(revision) is not int
        or type(account_revision) is not int
        or target not in ("active", "paused", "revoked")
    ):
        raise PeopleError("State and current assignment/account revisions are required")
    with closing(store.connect()) as db, db:
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        found = db.execute(
            "SELECT body FROM platform_assignments WHERE id=?", (assignment_id,)
        ).fetchone()
        if not found:
            raise PeopleError("Platform assignment not found", 404)
        row = json.loads(found[0])
        if row["revision"] != revision or row["state"] == "revoked":
            raise PeopleError("Assignment changed or permanently revoked", 409)
        if row["state"] == "requested" and target == "paused":
            raise PeopleError(
                "Requested assignment must be activated or revoked first", 409
            )
        if target == "active":
            if row["agent_id"] not in {item["id"] for item in agents()}:
                raise PeopleError("Configured agent no longer exists", 404)
            account = db.execute(
                "SELECT body,revision FROM social_accounts WHERE id=?",
                (row["account_id"],),
            ).fetchone()
            if (
                not account
                or account[1] != account_revision
                or json.loads(account[0])["status"] != "active"
            ):
                raise PeopleError(
                    "Registered platform account changed or inactive", 409
                )
            other = db.execute(
                "SELECT id FROM platform_assignments WHERE account_id=? AND state='active' AND id<>?",
                (row["account_id"], assignment_id),
            ).fetchone()
            if other:
                raise PeopleError(
                    "Platform account already has an active agent assignment; pause or revoke it first",
                    409,
                )
            row["account_revision"] = account_revision
        row.update(state=target, revision=revision + 1)
        db.execute(
            "UPDATE platform_assignments SET state=?,revision=?,body=? WHERE id=?",
            (target, row["revision"], json.dumps(row), assignment_id),
        )
        event(db, row, reason)
    return row


def history(store, assignment_id):
    with closing(store.connect()) as db, db:
        schema(db)
        return [
            json.loads(row[0])
            for row in db.execute(
                "SELECT body FROM platform_assignment_history WHERE assignment_id=? ORDER BY id",
                (assignment_id,),
            )
        ]
