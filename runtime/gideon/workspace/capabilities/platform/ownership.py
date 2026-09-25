"""Persistent project feature ownership with atomic handover history."""

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone

from gideon.core.config.loader import config_dir
from gideon.engine.tasks.hierarchy import HierarchyStore


class OwnershipError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _connect():
    path = config_dir() / "capabilities/platform/feature_ownership.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS ownership(project_id TEXT, feature TEXT, owner TEXT, revision INTEGER NOT NULL, PRIMARY KEY(project_id, feature));
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, feature TEXT, actor TEXT, action TEXT, revision INTEGER, at TEXT);
        CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, fingerprint TEXT, response TEXT);
        CREATE TRIGGER IF NOT EXISTS immutable_events_update BEFORE UPDATE ON events BEGIN SELECT RAISE(ABORT, 'immutable ownership history'); END;
        CREATE TRIGGER IF NOT EXISTS immutable_events_delete BEFORE DELETE ON events BEGIN SELECT RAISE(ABORT, 'immutable ownership history'); END;
    """)
    return connection


def view(project_id=None, feature=None):
    projects = HierarchyStore().list_projects()
    connection = _connect()
    try:
        rows = [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM ownership ORDER BY project_id,feature"
            )
        ]
        history = []
        if project_id is not None and feature is not None:
            history = [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM events WHERE project_id=? AND feature=? ORDER BY id DESC LIMIT 100",
                    (project_id, feature),
                )
            ]
        return {
            "version": 1,
            "projects": [
                {"id": project.id, "name": project.name} for project in projects
            ],
            "ownership": rows,
            "history": history,
        }
    finally:
        connection.close()


def mutate(body, actor):
    fields = {"project_id", "feature", "action", "revision", "request_id"}
    if not actor:
        raise OwnershipError("Authenticated ownership actor required", 403)
    if not isinstance(body, dict) or set(body) != fields:
        raise OwnershipError(
            "Project, feature, action, revision and request ID are required"
        )
    project, feature, action, revision, request = (
        body[key]
        for key in ("project_id", "feature", "action", "revision", "request_id")
    )
    if not isinstance(project, str) or HierarchyStore().get_project(project) is None:
        raise OwnershipError("Unknown project", 404)
    if not isinstance(feature, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._/-]{0,99}", feature
    ):
        raise OwnershipError("Invalid feature key")
    if action not in {"claim", "release"} or type(revision) is not int or revision < 0:
        raise OwnershipError("Invalid ownership action or revision")
    if not isinstance(request, str) or not re.fullmatch(
        r"[A-Za-z0-9-]{8,100}", request
    ):
        raise OwnershipError("Invalid request ID")
    fingerprint = hashlib.sha256(
        json.dumps([actor, body], sort_keys=True).encode()
    ).hexdigest()
    connection = _connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        replay = connection.execute(
            "SELECT * FROM requests WHERE id=?", (request,)
        ).fetchone()
        if replay:
            if replay["fingerprint"] != fingerprint:
                raise OwnershipError(
                    "Request ID was already used for different input or actor", 409
                )
            return json.loads(replay["response"])
        row = connection.execute(
            "SELECT * FROM ownership WHERE project_id=? AND feature=?",
            (project, feature),
        ).fetchone()
        current_revision = row["revision"] if row else 0
        owner = row["owner"] if row else None
        if revision != current_revision:
            raise OwnershipError("Ownership changed; refresh before continuing", 409)
        if action == "claim" and owner and owner != actor:
            raise OwnershipError("Feature already has an owner", 409)
        if action == "release" and owner != actor:
            raise OwnershipError("Only the current owner can release this feature", 403)
        result = {
            "project_id": project,
            "feature": feature,
            "owner": actor if action == "claim" else None,
            "revision": revision + 1,
        }
        connection.execute(
            "INSERT INTO ownership VALUES(?,?,?,?) ON CONFLICT(project_id,feature) DO UPDATE SET owner=excluded.owner,revision=excluded.revision",
            tuple(result.values()),
        )
        connection.execute(
            "INSERT INTO events(project_id,feature,actor,action,revision,at) VALUES(?,?,?,?,?,?)",
            (
                project,
                feature,
                actor,
                action,
                result["revision"],
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        connection.execute(
            "INSERT INTO requests VALUES(?,?,?)",
            (request, fingerprint, json.dumps(result)),
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
