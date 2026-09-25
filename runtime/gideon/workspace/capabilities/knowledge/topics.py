"""Saved keyword topics project current evidence from canonical personal sources."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.workspace.capabilities.communications.store import PeopleStore

from .capture import CaptureError, CaptureInbox, request_key, text_field
from .typed import BoundHierarchy, BoundTasks

SOURCE_TYPES = ("note", "journal", "fleeting", "memory", "person", "project", "task")
SCAN_LIMIT = 1000


def page_bounds(limit, offset):
    if (
        type(limit) is not int
        or type(offset) is not int
        or not 1 <= limit <= 100
        or not 0 <= offset <= 1000000
    ):
        raise CaptureError("Invalid topic pagination")


class TopicTasks(BoundTasks):
    def _derive_project_label(self, task_list_id, cache=None):
        if (
            not (self.home / "tasks/task_lists").is_dir()
            or not (self.home / "projects").is_dir()
        ):
            return ""
        return super()._derive_project_label(task_list_id, cache)


class TrackedTopics:
    def __init__(self, store, memory=None, home=None):
        self.store, self.db, self.memory = store, store.db, memory
        self.home = (
            Path(home).resolve() if home is not None else CaptureInbox._runtime_home()
        )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_topics (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS capability_knowledge_topic_mutations (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, result TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS topic_receipt_update BEFORE UPDATE ON capability_knowledge_topic_mutations
            BEGIN SELECT RAISE(ABORT,'topic mutation receipt is immutable'); END;
            CREATE TRIGGER IF NOT EXISTS topic_receipt_delete BEFORE DELETE ON capability_knowledge_topic_mutations
            BEGIN SELECT RAISE(ABORT,'topic mutation receipt is immutable'); END;
        """)

    def get(self, identity):
        row = self.db.execute(
            "SELECT body FROM capability_knowledge_topics WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise CaptureError("Tracked topic not found", 404)
        return json.loads(row[0])

    def list(self, limit=20, offset=0):
        page_bounds(limit, offset)
        total = self.db.execute(
            "SELECT count(*) FROM capability_knowledge_topics"
        ).fetchone()[0]
        items = [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT body FROM capability_knowledge_topics ORDER BY rowid DESC LIMIT ? OFFSET ?",
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

    def _mutation(self, payload, operation):
        key = request_key(payload.get("request_id"))
        packed = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            prior = self.db.execute(
                "SELECT payload,result FROM capability_knowledge_topic_mutations WHERE request_id=?",
                (key,),
            ).fetchone()
            if prior:
                if prior[0] != packed:
                    raise CaptureError(
                        "Topic request already belongs to different input", 409
                    )
                result = json.loads(prior[1])
            else:
                if CaptureInbox._runtime_home() != self.home:
                    raise CaptureError(
                        "Runtime home changed; restore the bound allocation before editing topics",
                        409,
                    )
                result = operation()
                self.db.execute(
                    "INSERT INTO capability_knowledge_topic_mutations VALUES (?,?,?)",
                    (key, packed, json.dumps(result, ensure_ascii=False)),
                )
            self.db.commit()
            return result
        except Exception:
            self.db.rollback()
            raise

    def save(self, body):
        if not isinstance(body, dict) or set(body) not in (
            {"request_id", "name", "query", "source_types"},
            {"request_id", "id", "revision", "name", "query", "source_types"},
        ):
            raise CaptureError(
                "Topic save requires name, query, source_types, request_id and optional paired id/revision"
            )
        if "id" in body:
            text_field(body["id"], "id", 128)
        name, query = text_field(body["name"], "name", 100), text_field(
            body["query"], "query", 300
        )
        sources = body["source_types"]
        if (
            not isinstance(sources, list)
            or not sources
            or any(
                not isinstance(kind, str) or kind not in SOURCE_TYPES
                for kind in sources
            )
            or len(set(sources)) != len(sources)
        ):
            raise CaptureError("Choose distinct supported source types")
        if not re.findall(r"\w+", query):
            raise CaptureError("Topic query must contain searchable words")

        def apply():
            previous = self.get(body["id"]) if "id" in body else None
            if previous and (
                type(body["revision"]) is not int
                or body["revision"] != previous["revision"]
            ):
                raise CaptureError("Tracked topic changed; reload before editing", 409)
            if previous is None and self.list(limit=1)["total"] >= 1000:
                raise CaptureError("At most 1000 tracked topics are supported")
            now = datetime.now(timezone.utc).isoformat()
            result = {
                "id": previous["id"] if previous else str(uuid4()),
                "name": name,
                "query": query,
                "source_types": sources,
                "revision": previous["revision"] + 1 if previous else 1,
                "created_at": previous["created_at"] if previous else now,
                "updated_at": now,
            }
            self.db.execute(
                "INSERT INTO capability_knowledge_topics VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
                (result["id"], json.dumps(result, ensure_ascii=False)),
            )
            return result

        return self._mutation(body, apply)

    def delete(self, identity, body):
        if not isinstance(body, dict) or set(body) != {"request_id", "revision"}:
            raise CaptureError("Topic delete requires request_id and revision only")

        def apply():
            previous = self.get(identity)
            if (
                type(body["revision"]) is not int
                or previous["revision"] != body["revision"]
            ):
                raise CaptureError("Tracked topic changed; reload before deleting", 409)
            self.db.execute(
                "DELETE FROM capability_knowledge_topics WHERE id=?", (identity,)
            )
            return {"id": identity, "deleted": True}

        return self._mutation({**body, "id": identity, "operation": "delete"}, apply)

    def _rows(self, kind):
        if kind in ("note", "journal", "fleeting"):
            rows = self.db.execute(
                'SELECT id,title,content FROM items WHERE item_type=? AND COALESCE(is_archived,0)=0 AND status="active" ORDER BY id LIMIT ?',
                (kind, SCAN_LIMIT + 1),
            ).fetchall()
            return [
                {
                    "id": row[0],
                    "title": row[1] or "",
                    "text": row[2] or "",
                    "link": "#/knowledge/item/" + row[0],
                }
                for row in rows
            ]
        if kind == "memory":
            if self.memory is None:
                return None
            return [
                {
                    "id": row["id"],
                    "title": "Memory",
                    "text": row.get("text", ""),
                    "link": "#/capabilities/knowledge?memory=" + row["id"],
                }
                for row in self.memory.episodic_list(limit=SCAN_LIMIT + 1)
            ]
        if kind == "person":
            root = self.home / "capabilities/communications"
            if not (root / "people.sqlite3").is_file():
                return []
            return [
                {
                    "id": row["id"],
                    "title": row["name"],
                    "text": row["notes"]
                    + " "
                    + " ".join(item["value"] for item in row["identities"]),
                    "link": "#/capabilities/communications?person=" + row["id"],
                }
                for row in PeopleStore(root).people()[: SCAN_LIMIT + 1]
            ]
        if kind == "project":
            if not (self.home / "projects").is_dir():
                return []
            return [
                {
                    "id": row.id,
                    "title": row.name,
                    "text": row.brief,
                    "link": "#/projects/" + row.id,
                }
                for row in BoundHierarchy(self.home)._all_projects_raw()
                if row.status != "archived"
            ][: SCAN_LIMIT + 1]
        if not (self.home / "tasks").is_dir():
            return []
        return [
            {
                "id": row.id,
                "title": row.title,
                "text": row.description,
                "link": "#/tasks?open=" + row.id,
            }
            for row in TopicTasks(self.home)._all_tasks()[: SCAN_LIMIT + 1]
        ]

    def matches(self, identity, limit=20, offset=0):
        page_bounds(limit, offset)
        topic, items, statuses, scanned, truncated = self.get(identity), [], {}, {}, []
        terms = list(dict.fromkeys(re.findall(r"\w+", topic["query"].casefold())))
        for kind in topic["source_types"]:
            rows = self._rows(kind)
            statuses[kind] = "unavailable" if rows is None else "available"
            rows = rows or []
            scanned[kind] = min(len(rows), SCAN_LIMIT)
            if len(rows) > SCAN_LIMIT:
                truncated.append(kind)
            for row in rows[:SCAN_LIMIT]:
                text = row["title"] + " " + row["text"]
                if all(term in text.casefold() for term in terms):
                    items.append(
                        {
                            "source_type": kind,
                            "source_id": row["id"],
                            "title": row["title"],
                            "excerpt": row["text"][:400],
                            "source_link": row["link"],
                            "matched_terms": terms,
                        }
                    )
        items.sort(key=lambda row: (row["source_type"], row["source_id"]))
        total = len(items)
        return {
            "topic": topic,
            "items": items[offset : offset + limit],
            "sources": statuses,
            "scanned": scanned,
            "truncated": truncated,
            "total": total,
            "total_is_complete": not truncated
            and "unavailable" not in statuses.values(),
            "limit": limit,
            "offset": offset,
            "next_offset": offset + limit if offset + limit < total else None,
        }
