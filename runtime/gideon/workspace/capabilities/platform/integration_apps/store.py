"""Persistent named integrations, reviewed API actions and repository annotations."""

import asyncio
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.sdk.credentials import CredentialStore
from gideon.workspace.capabilities.media.jobs import identity
from gideon.workspace.capabilities.music.listening import digest
from gideon.workspace.capabilities.music.store import DomainError, integer

from .client import execute_remote
from .contracts import configuration, fields, plan, repository


def now():
    return datetime.now(timezone.utc).isoformat()


def jira_report(body):
    if not isinstance(body, dict) or not isinstance(body.get("issues"), list):
        raise DomainError("Issue search response required")
    statuses = {}
    assignees = {}
    total = 0
    for issue in body["issues"]:
        if not isinstance(issue, dict) or not isinstance(issue.get("fields"), dict):
            raise DomainError("Invalid issue response")
        values = issue["fields"]
        status = (values.get("status") or {}).get("name") or "Unknown"
        assignee = (values.get("assignee") or {}).get("displayName") or "Unassigned"
        statuses[status] = statuses.get(status, 0) + 1
        assignees[assignee] = assignees.get(assignee, 0) + 1
        total += 1
    return {
        "observed_issues": total,
        "by_status": statuses,
        "by_assignee": assignees,
        "partial": bool(body.get("nextPageToken") or body.get("isLast") is False),
    }


class IntegrationApps:
    def __init__(self, home, credential_resolver=None):
        self.home = Path(home)
        self.path = self.home / "capabilities/platform/integration_apps.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.credentials = (
            CredentialStore(home) if credential_resolver is None else None
        )
        self.resolve = credential_resolver or self._resolve
        with self.db() as db:
            db.execute("PRAGMA user_version=1")
            db.execute(
                "CREATE TABLE IF NOT EXISTS connections(id TEXT PRIMARY KEY,payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,fingerprint TEXT,payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS repositories(connection_id TEXT,name TEXT,payload TEXT,PRIMARY KEY(connection_id,name))"
            )

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _resolve(self, name):
        self.credentials.reload()
        try:
            return self.credentials.resolve(name).secret
        except KeyError:
            return None

    def connection(self, item_id):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM connections WHERE id=?", (item_id,)
            ).fetchone()
        if not row:
            raise DomainError("Connection not found", 404)
        return json.loads(row[0])

    def connections(self):
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM connections ORDER BY rowid DESC"
                )
            ]

    def save_connection(self, data, item_id=None):
        if item_id:
            fields(
                data,
                (
                    "revision",
                    "kind",
                    "label",
                    "endpoint",
                    "credential_name",
                    "aux_credential_name",
                    "username",
                ),
            )
            revision = integer(data["revision"], "revision", 1, 1000000)
        else:
            revision = 0
        normalized = configuration(
            {key: value for key, value in data.items() if key != "revision"}
        )
        with self.db() as db:
            if item_id:
                row = db.execute(
                    "SELECT payload FROM connections WHERE id=?", (item_id,)
                ).fetchone()
                if not row:
                    raise DomainError("Connection not found", 404)
                if json.loads(row[0])["revision"] != revision:
                    raise DomainError("Connection revision changed", 409)
            value = {
                **normalized,
                "id": item_id or str(uuid4()),
                "revision": revision + 1,
            }
            db.execute(
                "INSERT OR REPLACE INTO connections VALUES (?,?)",
                (value["id"], json.dumps(value)),
            )
            return value

    def prepare(self, connection_id, data):
        fields(data, ("request_id", "operation", "input"))
        if (
            not isinstance(data["request_id"], str)
            or not 1 <= len(data["request_id"]) <= 100
        ):
            raise DomainError("Request ID required")
        connection = self.connection(connection_id)
        request = plan(connection["kind"], data["operation"], data["input"])
        fingerprint = digest([connection_id, data])
        with self.db() as db:
            previous = db.execute(
                "SELECT fingerprint,payload FROM runs WHERE request_id=?",
                (data["request_id"],),
            ).fetchone()
            if previous:
                if previous[0] != fingerprint:
                    raise DomainError("Request ID already used", 409)
                return json.loads(previous[1])
            item = {
                "id": str(uuid4()),
                "connection_id": connection_id,
                "connection_revision": connection["revision"],
                "request": request,
                "input": data["input"],
                "status": "prepared",
                "revision": 1,
                "created_at": now(),
                "updated_at": now(),
                "result": None,
                "error": None,
            }
            db.execute(
                "INSERT INTO runs VALUES (?,?,?,?)",
                (item["id"], data["request_id"], fingerprint, json.dumps(item)),
            )
            return item

    def get(self, item_id):
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM runs WHERE id=?", (item_id,)
            ).fetchone()
        if not row:
            raise DomainError("Run not found", 404)
        return json.loads(row[0])

    def runs(self):
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM runs ORDER BY rowid DESC LIMIT 100"
                )
            ]

    async def execute(self, item_id, data):
        fields(data, ("revision", "confirm"))
        if data["confirm"] is not True:
            raise DomainError("Explicit reviewed execution confirmation required", 409)
        integer(data["revision"], "revision", 1, 1000000)
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM runs WHERE id=?", (item_id,)
            ).fetchone()
            if not row:
                raise DomainError("Run not found", 404)
            item = json.loads(row[0])
            if item["status"] == "succeeded":
                return item
            if item["revision"] != data["revision"] or item["status"] != "prepared":
                raise DomainError(
                    "Run cannot be repeated; inspect receipt before preparing another action",
                    409,
                )
            connection_row = db.execute(
                "SELECT payload FROM connections WHERE id=?", (item["connection_id"],)
            ).fetchone()
            connection = json.loads(connection_row[0])
            if connection["revision"] != item["connection_revision"]:
                raise DomainError("Connection changed after review", 409)
            item.update(
                status="running",
                revision=2,
                updated_at=now(),
                owner_pid=os.getpid(),
                owner_identity=identity(os.getpid()),
            )
            db.execute(
                "UPDATE runs SET payload=? WHERE id=?", (json.dumps(item), item_id)
            )
        try:
            result = await execute_remote(connection, item["request"], self.resolve)
            if item["request"]["operation"] in (
                "jira_search",
                "jira_epics",
                "jira_epic_children",
            ):
                result["report"] = jira_report(result["body"])
            if item["request"]["operation"] == "github_repos":
                self.ingest_repos(connection["id"], result["body"])
            item.update(status="succeeded", result=result)
        except asyncio.CancelledError:
            item.update(
                status="uncertain",
                error="Execution cancelled; remote outcome requires inspection",
            )
            raise
        except Exception as exc:
            unavailable = (
                isinstance(exc, DomainError)
                and getattr(exc, "code", "") == "credential_unavailable"
            )
            item.update(
                status=(
                    "failed"
                    if unavailable or not item["request"]["mutates"]
                    else "uncertain"
                ),
                error=(
                    str(exc)
                    if isinstance(exc, DomainError)
                    else "API execution failed; inspect remote state before repeating"
                ),
            )
        finally:
            item.update(revision=3, updated_at=now())
            item.pop("owner_pid", None)
            item.pop("owner_identity", None)
            with self.db() as db:
                db.execute(
                    "UPDATE runs SET payload=? WHERE id=?", (json.dumps(item), item_id)
                )
        return item

    def ingest_repos(self, connection_id, rows):
        if not isinstance(rows, list):
            raise DomainError("Invalid GitHub repository response", 502)
        normalized = []
        for row in rows:
            if not isinstance(row, dict):
                raise DomainError("Invalid repository row", 502)
            normalized.append((repository(row.get("full_name")), row))
        with self.db() as db:
            for name, remote in normalized:
                prior = db.execute(
                    "SELECT payload FROM repositories WHERE connection_id=? AND name=?",
                    (connection_id, name),
                ).fetchone()
                old = json.loads(prior[0]) if prior else {}
                value = {
                    "name": name,
                    "connection_id": connection_id,
                    "remote": remote,
                    "flags": old.get("flags", {}),
                    "secret_refs": old.get("secret_refs", {}),
                    "revision": old.get("revision", 0) + 1,
                }
                db.execute(
                    "INSERT OR REPLACE INTO repositories VALUES (?,?,?)",
                    (connection_id, name, json.dumps(value)),
                )

    def repositories(self, connection_id):
        self.connection(connection_id)
        with self.db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM repositories WHERE connection_id=? ORDER BY name",
                    (connection_id,),
                )
            ]

    def annotate(self, connection_id, name, data):
        fields(data, ("revision", "flags", "secret_refs"))
        repository(name)
        if (
            not isinstance(data["flags"], dict)
            or len(data["flags"]) > 30
            or any(
                not isinstance(key, str) or len(key) > 100 or type(value) is not bool
                for key, value in data["flags"].items()
            )
        ):
            raise DomainError("Invalid repository flags")
        if not isinstance(data["secret_refs"], dict) or len(data["secret_refs"]) > 30:
            raise DomainError("Invalid secret assignments")
        for key, ref in data["secret_refs"].items():
            plan(
                "github",
                "github_secret_sync",
                {"repository": name, "name": key, "credential_ref": ref},
            )
        with self.db() as db:
            row = db.execute(
                "SELECT payload FROM repositories WHERE connection_id=? AND name=?",
                (connection_id, name),
            ).fetchone()
            if not row:
                raise DomainError("Repository not synchronized", 404)
            item = json.loads(row[0])
            if item["revision"] != data["revision"]:
                raise DomainError("Repository revision changed", 409)
            item.update(
                flags=data["flags"],
                secret_refs=data["secret_refs"],
                revision=item["revision"] + 1,
            )
            db.execute(
                "UPDATE repositories SET payload=? WHERE connection_id=? AND name=?",
                (json.dumps(item), connection_id, name),
            )
            return item
