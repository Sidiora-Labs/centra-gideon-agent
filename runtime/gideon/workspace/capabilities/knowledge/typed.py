"""Reviewed capture classification into existing canonical personal stores."""

import csv
import hashlib
import io
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.config.loader import CONFIG_DIR_NAME, config_dir
from gideon.core.config.locations import configuration_home
from gideon.core.sqlite_compat import sqlite3
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.native import NativeTaskProvider
from gideon.workspace.capabilities.communications import imports
from gideon.workspace.capabilities.communications.store import (
    PeopleStore,
    person_values,
)

from .capture import CaptureError, CaptureInbox, request_key, text_field

FIELDS = {
    "person": ("name", "notes", "identities"),
    "project": ("name", "brief"),
    "idea": ("title", "content"),
    "admin": ("title", "description"),
    "memory": ("text",),
}
DESTINATIONS = {
    "person": "people",
    "project": "projects",
    "idea": "fleeting",
    "admin": "tasks",
    "memory": "episodic",
}


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class BoundHierarchy(HierarchyStore):
    def __init__(self, home, identity=None):
        self.home, self.identity = Path(home), identity

    def _base(self):
        return self.home / "tasks"

    def _projects_dir(self):
        path = self.home / "projects"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _lists_dir(self):
        path = self._base() / "task_lists"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _write_project(self, project):
        if self.identity:
            project.id, self.identity = self.identity, None
        super()._write_project(project)


class BoundTasks(NativeTaskProvider):
    def __init__(self, home, identity=None):
        self.home, self.identity = Path(home), identity

    def _ensure_dir(self):
        path = self.home / "tasks"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _derive_project_label(self, task_list_id, cache=None):
        if not task_list_id:
            return ""
        hierarchy = BoundHierarchy(self.home)
        task_list = hierarchy.get_task_list(task_list_id)
        project = hierarchy.get_project(task_list.project_id) if task_list else None
        return project.name if project else ""

    def _write_task(self, task):
        if self.identity:
            task.id, self.identity = self.identity, None
        super()._write_task(task)


class TypedCapture:
    def __init__(self, store, memory=None, home=None):
        self.home = Path(home if home is not None else config_dir()).resolve()
        self.inbox, self.memory = CaptureInbox(store, home=self.home), memory
        self.db, self.store = store.db, store
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_types (
                capture_id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL, receipt TEXT
            );
            CREATE TRIGGER IF NOT EXISTS typed_original_immutable BEFORE UPDATE OF capture_id,request_id,payload
            ON capability_knowledge_types BEGIN SELECT RAISE(ABORT,'typed provenance is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS typed_receipt_immutable BEFORE UPDATE OF receipt ON capability_knowledge_types
            WHEN OLD.receipt IS NOT NULL BEGIN SELECT RAISE(ABORT,'typed receipt is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS typed_receipt_delete BEFORE DELETE ON capability_knowledge_types
            BEGIN SELECT RAISE(ABORT,'typed provenance is immutable'); END;
        """)
        self._idea_index(allow_conflicts=True)

    def _idea_index(self, allow_conflicts=False):
        collisions = [
            row[0]
            for row in self.db.execute(
                "SELECT guid FROM items WHERE substr(guid,1,14) = 'typed_capture:' GROUP BY guid HAVING count(*) > 1"
            )
        ]
        if collisions:
            if allow_conflicts:
                return False
            raise CaptureError(
                "Idea import identity conflicts require review of existing records: "
                + ", ".join(collisions),
                409,
            )
        try:
            self.db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS typed_capture_guid ON items(guid) WHERE substr(guid,1,14) = 'typed_capture:'"
            )
            self.db.commit()
        except sqlite3.IntegrityError:
            self.db.rollback()
            if allow_conflicts:
                return False
            raise CaptureError(
                "Idea import identities changed concurrently; review existing records and retry",
                409,
            ) from None
        return True

    def preview(self, body):
        if not isinstance(body, dict) or set(body) != {"capture_id", "kind", "fields"}:
            raise CaptureError("Preview requires capture_id, kind and fields only")
        kind, fields = body["kind"], body["fields"]
        if (
            not isinstance(kind, str)
            or kind not in FIELDS
            or not isinstance(fields, dict)
            or len(encoded(fields)) > 100000
        ):
            raise CaptureError("Invalid type or original fields")
        capture = self.inbox.get(body["capture_id"])
        mapped = {key: fields[key] for key in FIELDS[kind] if key in fields}
        if kind == "person":
            validated = person_values(mapped)
            mapped = {key: validated[key] for key in FIELDS[kind]}
            if any("|" in item["value"] for item in mapped["identities"]):
                raise CaptureError("Identity values containing | cannot be imported")
        else:
            for index, key in enumerate(FIELDS[kind]):
                mapped[key] = text_field(
                    mapped.get(key, ""),
                    key,
                    300 if key in ("name", "title") else 100000,
                    required=index == 0,
                )
        stable = {
            **body,
            "revision": capture["revision"],
            "mapped_fields": mapped,
            "unsupported_fields": sorted(set(fields) - set(FIELDS[kind])),
            "destination": DESTINATIONS[kind],
        }
        available = kind != "memory" or self.memory is not None
        return {
            **stable,
            "preview_id": digest(stable),
            "available": available,
            "unavailable_reason": (
                "" if available else "The bound memory service is unavailable"
            ),
        }

    def list(self, limit=20, offset=0):
        if (
            type(limit) is not int
            or type(offset) is not int
            or not 1 <= limit <= 100
            or not 0 <= offset <= 1000000
        ):
            raise CaptureError("Invalid pagination")
        total = self.db.execute(
            "SELECT count(*) FROM capability_knowledge_types WHERE receipt IS NOT NULL"
        ).fetchone()[0]
        items = [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT receipt FROM capability_knowledge_types WHERE receipt IS NOT NULL ORDER BY rowid DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
        ]
        return {
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        }

    async def commit(self, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "preview_id",
            "capture_id",
            "revision",
            "kind",
            "fields",
        }:
            raise CaptureError(
                "Commit requires the reviewed preview and request_id only"
            )
        request_key(body["request_id"])
        payload = encoded(body)
        previous = self.db.execute(
            "SELECT * FROM capability_knowledge_types WHERE capture_id=? OR request_id=?",
            (body["capture_id"], body["request_id"]),
        ).fetchone()
        if previous:
            if previous["payload"] != payload:
                raise CaptureError(
                    "This capture or request already has a different reviewed import",
                    409,
                )
            if previous["receipt"]:
                return json.loads(previous["receipt"])
        preview = self.preview(
            {key: body[key] for key in ("capture_id", "kind", "fields")}
        )
        if (
            type(body["revision"]) is not int
            or preview["revision"] != body["revision"]
            or preview["preview_id"] != body["preview_id"]
        ):
            raise CaptureError("Capture or reviewed fields changed; preview again", 409)
        if not preview["available"]:
            raise CaptureError(preview["unavailable_reason"], 503)
        self.inbox.assert_write_scope()
        if body["kind"] == "idea":
            self._idea_index()
        if not previous:
            self.db.execute(
                "INSERT OR IGNORE INTO capability_knowledge_types VALUES (?,?,?,NULL)",
                (body["capture_id"], body["request_id"], payload),
            )
            self.db.commit()
            reserved = self.db.execute(
                "SELECT * FROM capability_knowledge_types WHERE capture_id=? OR request_id=?",
                (body["capture_id"], body["request_id"]),
            ).fetchone()
            if reserved["payload"] != payload:
                raise CaptureError(
                    "This capture or request already has a different reviewed import",
                    409,
                )
            if reserved["receipt"]:
                return json.loads(reserved["receipt"])
        target, link = await self._destination(preview)
        receipt = {
            key: body[key] for key in ("request_id", "preview_id", "capture_id", "kind")
        }
        receipt.update(
            destination=preview["destination"],
            destination_id=target,
            source_link=link,
            original_fields=body["fields"],
            unsupported_fields=preview["unsupported_fields"],
            original_capture=self.inbox.get(body["capture_id"]),
            committed_at=datetime.now(timezone.utc).isoformat(),
        )
        self.db.execute(
            "UPDATE capability_knowledge_types SET receipt=? WHERE capture_id=? AND receipt IS NULL",
            (encoded(receipt), body["capture_id"]),
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT receipt FROM capability_knowledge_types WHERE capture_id=?",
            (body["capture_id"],),
        ).fetchone()
        return json.loads(row[0])

    async def _destination(self, preview):
        kind, fields = preview["kind"], preview["mapped_fields"]
        if (
            kind in ("project", "admin")
            and configuration_home(
                os.environ.get("GIDEON_HOME"),
                Path.home() / CONFIG_DIR_NAME,
                logging.getLogger(__name__),
            ).resolve()
            != self.home
        ):
            raise CaptureError(
                "Runtime home changed; restore the bound allocation before importing projects or tasks",
                409,
            )
        marker = "typed_capture:" + preview["capture_id"]
        identity = digest({"capture": preview["capture_id"], "kind": kind})[:24]
        if kind == "person":
            buffer = io.StringIO()
            writer = csv.DictWriter(
                buffer, fieldnames=["name", "notes", "email", "phone", "handle"]
            )
            writer.writeheader()
            writer.writerow(
                {
                    **{key: fields[key] for key in ("name", "notes")},
                    **{
                        key: "|".join(
                            item["value"]
                            for item in fields["identities"]
                            if item["kind"] == key
                        )
                        for key in ("email", "phone", "handle")
                    },
                }
            )
            data = {"format": "csv", "content": buffer.getvalue()}
            people = PeopleStore(root=self.home / "capabilities/communications")
            receipt, _ = imports.commit(
                people,
                {
                    **data,
                    "source_digest": imports.preview(people, data)["source_digest"],
                    "decisions": [{"row_id": "1", "action": "create"}],
                },
            )
            target = receipt["rows"][0]["person_id"]
            people.get(target)
            return target, "#/capabilities/communications?person=" + target
        if kind == "project":
            hierarchy = BoundHierarchy(self.home, "p-" + identity)
            project = hierarchy.get_project(
                "p-" + identity
            ) or hierarchy.create_project(**fields)
            return project.id, "#/projects/" + project.id
        if kind == "admin":
            tasks = BoundTasks(self.home, "t-" + identity)
            task = await tasks.get_task("t-" + identity) or await tasks.create_task(
                **fields
            )
            return task.id, "#/tasks?open=" + task.id
        if kind == "idea":
            existing = self.db.execute(
                "SELECT id FROM items WHERE guid=?", (marker,)
            ).fetchone()
            target = (
                existing[0]
                if existing
                else self.store.create_typed_item(
                    item_type="fleeting",
                    **fields,
                    guid=marker,
                    extra={
                        "file_metadata": {
                            "capture_id": preview["capture_id"],
                            "original_at": self.inbox.get(preview["capture_id"])[
                                "captured_at"
                            ],
                        }
                    },
                )
            )
            if target is None:
                recovered = self.db.execute(
                    "SELECT id FROM items WHERE guid=?", (marker,)
                ).fetchone()
                if recovered is None:
                    raise CaptureError(
                        "Canonical idea creation did not persist a destination", 409
                    )
                target = recovered[0]
            return target, "#/knowledge/item/" + target
        rows = self.memory.episodic_list(limit=2, tag_filter=[marker])
        if not rows:
            if not self.memory.write_episodic(
                fields["text"], source=marker, tags=[marker]
            ):
                raise CaptureError("The memory service declined this import", 409)
            rows = self.memory.episodic_list(limit=2, tag_filter=[marker])
        if len(rows) != 1:
            raise CaptureError(
                "No unique canonical memory destination was recorded", 409
            )
        return rows[0]["id"], "#/capabilities/knowledge?memory=" + rows[0]["id"]
