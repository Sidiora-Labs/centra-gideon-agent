"""Durable, non-mutating snapshots of real workspace context."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class ConflictError(ValueError):
    pass


def identifiers(value):
    if not isinstance(value, list) or len(value) > 256:
        raise ValueError("Reference IDs must be a list of at most 256 strings")
    if any(not isinstance(x, str) or not x.strip() or len(x) > 256 for x in value):
        raise ValueError("Invalid reference ID")
    if len(set(value)) != len(value):
        raise ValueError("Duplicate reference ID")
    return value


class SnapshotStore:
    def __init__(self, root, *, allowed_roots):
        self.root = Path(root)
        self.allowed_roots = [Path(p).resolve() for p in allowed_roots]
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "snapshots.sqlite3"
        with self._db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, request_id TEXT UNIQUE, input TEXT NOT NULL, payload TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0)")
        os.chmod(self.path, 0o600)

    def _db(self):
        return sqlite3.connect(self.path, timeout=10)

    def _workspace(self, raw):
        if not isinstance(raw, str) or not raw or len(raw) > 4096:
            raise ValueError("Workspace must be an accessible absolute path")
        path = Path(raw)
        if not path.is_absolute():
            raise ValueError("Workspace must be absolute")
        path = path.resolve(strict=True)
        if not any(path.is_relative_to(root) for root in self.allowed_roots):
            raise ValueError("Workspace outside allowed roots")
        if not path.is_dir() or not os.access(path, os.R_OK | os.X_OK):
            raise ValueError("Workspace is inaccessible")
        return path

    def _git(self, path, *args):
        result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(path), *args], capture_output=True, timeout=10,
                                env={"PATH": os.environ.get("PATH", ""), "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_OPTIONAL_LOCKS": "0"})
        if result.returncode:
            raise ValueError("Workspace Git context is unavailable")
        return result.stdout.decode("utf-8", "replace").strip()

    def _live(self, raw):
        path = self._workspace(raw)
        git_dir = self._workspace(self._git(path, "rev-parse", "--absolute-git-dir"))
        if not git_dir.is_dir():
            raise ValueError("Git directory is inaccessible")
        common = Path(self._git(path, "rev-parse", "--git-common-dir"))
        self._workspace(str(common if common.is_absolute() else path / common))
        head = self._git(path, "rev-parse", "--abbrev-ref", "HEAD")
        return {"branch": None if head == "HEAD" else head,
                "dirty": bool(self._git(path, "status", "--porcelain", "--untracked-files=normal"))}

    def capture(self, payload, *, terminal_ids, task_ids):
        if not isinstance(payload, dict) or set(payload) - {"project_id", "workspace", "terminal_ids", "task_ids", "request_id"}:
            raise ValueError("Invalid snapshot fields")
        project = payload.get("project_id")
        request_id = payload.get("request_id")
        if not isinstance(project, str) or not project.strip() or len(project) > 256:
            raise ValueError("Project ID is required")
        if not isinstance(request_id, str) or not request_id.strip() or len(request_id) > 256:
            raise ValueError("Request ID is required")
        normalized = {**payload, "workspace": str(self._workspace(payload.get("workspace"))),
                      "terminal_ids": identifiers(payload.get("terminal_ids", [])),
                      "task_ids": identifiers(payload.get("task_ids", []))}
        encoded = json.dumps(normalized, sort_keys=True)
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            previous = db.execute("SELECT input,payload,deleted FROM snapshots WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous[0] != encoded or previous[2]:
                    raise ConflictError("Request ID already used for another or deleted snapshot")
                return json.loads(previous[1])
            if set(normalized["terminal_ids"]) - set(terminal_ids) or set(normalized["task_ids"]) - set(task_ids):
                raise ValueError("Selected references are not live")
            record = {"id": uuid4().hex, "project_id": project, "workspace": normalized["workspace"],
                      **self._live(normalized["workspace"]), "terminal_ids": normalized["terminal_ids"],
                      "task_ids": normalized["task_ids"], "captured_at": datetime.now(timezone.utc).isoformat(), "revision": 1}
            db.execute("INSERT INTO snapshots(id,request_id,input,payload) VALUES(?,?,?,?)",
                       (record["id"], request_id, encoded, json.dumps(record)))
        return record

    def list(self, *, offset=0, limit=100):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("Invalid pagination")
        with self._db() as db:
            return [json.loads(row[0]) for row in db.execute("SELECT payload FROM snapshots WHERE deleted=0 ORDER BY rowid DESC LIMIT ? OFFSET ?", (limit, offset))]

    def get(self, snapshot_id):
        with self._db() as db:
            row = db.execute("SELECT payload FROM snapshots WHERE id=? AND deleted=0", (snapshot_id,)).fetchone()
        if row is None:
            raise FileNotFoundError("Snapshot not found")
        return json.loads(row[0])

    def delete(self, snapshot_id, revision):
        if type(revision) is not int:
            raise ValueError("Revision must be an integer")
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload,deleted FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if row is None:
                raise FileNotFoundError("Snapshot not found")
            if json.loads(row[0])["revision"] != revision:
                raise ConflictError("Snapshot revision changed")
            db.execute("UPDATE snapshots SET deleted=1 WHERE id=?", (snapshot_id,))
        return {"id": snapshot_id, "deleted": True}

    def reconcile(self, snapshot_id, *, terminal_ids, task_ids):
        snapshot = self.get(snapshot_id)
        live = self._live(snapshot["workspace"])
        result = {"snapshot": snapshot, "live": live, "branch_matches": None if snapshot["branch"] is None else snapshot["branch"] == live["branch"]}
        for kind, available in (("terminal", set(terminal_ids)), ("task", set(task_ids))):
            refs = snapshot[f"{kind}_ids"]
            result[f"surviving_{kind}_ids"] = [x for x in refs if x in available]
            result[f"missing_{kind}_ids"] = [x for x in refs if x not in available]
        return result
