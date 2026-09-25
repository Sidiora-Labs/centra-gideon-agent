"""Ordered idea-list exchange through canonical collections and the owned knowledge vault."""

import fcntl
import json
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path

from gideon.cognition.knowledge.vault import KnowledgeVault
from gideon.core.sqlite_compat import sqlite3

from .capture import CaptureError, request_key
from .idea_format import parse, preview, render
from .reviews import ReviewService, digest, packed


def serialized(function):
    @wraps(function)
    def invoke(self, *args, **kwargs):
        with (self.home / ".knowledge-ideas.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                return function(self, *args, **kwargs)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    return invoke


class IdeaLists:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.guard = ReviewService(store, home)
        self.home = self.guard.home
        try:
            config = json.loads((self.home / "config.json").read_text()).get(
                "knowledge", {}
            )
        except (OSError, ValueError, TypeError):
            config = {}
        configured = Path(
            str(config.get("vault_path") or "knowledge-vault")
        ).expanduser()
        self.vault_root = (
            configured if configured.is_absolute() else self.home / configured
        ).resolve()
        self.vault_available = config.get(
            "vault_mode"
        ) == "two_way" and self.vault_root.is_relative_to(self.home)
        self.vault = KnowledgeVault(store, self.vault_root, mode="two_way")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS capability_knowledge_ideas (id TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS capability_knowledge_idea_requests (request_id TEXT PRIMARY KEY, payload TEXT NOT NULL, list_id TEXT NOT NULL, receipt TEXT);
            CREATE UNIQUE INDEX IF NOT EXISTS idea_list_guid ON items(guid) WHERE substr(guid,1,10)='idea_list:';
            CREATE UNIQUE INDEX IF NOT EXISTS idea_collection_identity ON collections(query) WHERE substr(query,1,10)='idea-list:' AND kind='manual';
            CREATE TRIGGER IF NOT EXISTS idea_receipt_immutable BEFORE UPDATE ON capability_knowledge_idea_requests
            WHEN OLD.receipt IS NOT NULL BEGIN SELECT RAISE(ABORT,'idea receipt is immutable'); END;
            CREATE UNIQUE INDEX IF NOT EXISTS idea_request_pending ON capability_knowledge_idea_requests(list_id) WHERE receipt IS NULL;
        """)

    def availability(self):
        try:
            config = json.loads((self.home / "config.json").read_text()).get(
                "knowledge", {}
            )
            configured = Path(
                str(config.get("vault_path") or "knowledge-vault")
            ).expanduser()
            current = (
                configured if configured.is_absolute() else self.home / configured
            ).resolve()
            available = (
                config.get("vault_mode") == "two_way"
                and current == self.vault_root
                and current.is_relative_to(self.home)
            )
        except (OSError, ValueError, TypeError):
            available = False
        return {
            "available": available,
            "reason": (
                ""
                if available
                else "Enable two-way Knowledge vault in existing settings with a path inside this runtime home, then reload the capability."
            ),
        }

    def state(self, identity):
        row = self.db.execute(
            "SELECT body FROM capability_knowledge_ideas WHERE id=?", (identity,)
        ).fetchone()
        if row is None:
            raise CaptureError("Idea list not found", 404)
        return json.loads(row[0])

    def put(self, state):
        self.db.execute(
            "INSERT INTO capability_knowledge_ideas VALUES (?,?) ON CONFLICT(id) DO UPDATE SET body=excluded.body",
            (state["id"], packed(state)),
        )
        self.db.commit()

    def get(self, identity):
        state = self.state(identity)
        if state.get("trigger_id"):
            from gideon.automation.triggers.store import TriggerStore

            trigger = TriggerStore(self.home).get(state["trigger_id"])
            state["next_fire_at"] = trigger.trigger.next_fire_at if trigger else ""
            state["sync_enabled"] = trigger.trigger.enabled if trigger else False
        source = self.store.get_item(state["source_id"])
        collection = self.store.get_collection(state["collection_id"])
        if source is None:
            raise CaptureError("Canonical idea-list source was deleted", 409)
        document = parse(source["content"])
        items = [self.store.get_item(item) for item in state["member_ids"]]
        items = [item for item in items if item and not item.get("is_archived")]
        document["ideas"] = [item["content"] for item in items]
        projection = [
            {
                "id": item["id"],
                "title": item["title"],
                "content": item["content"],
                "source_link": "#/knowledge/item/" + item["id"],
            }
            for item in items
        ]
        current_hash = digest(
            {
                "source": source["content"],
                "items": projection,
                "collection": collection["name"] if collection else None,
            }
        )
        ledger = self.store.vault_projection(source["id"]) or {}
        status = (
            "collection_deleted"
            if collection is None
            else (
                "owner_deleted"
                if ledger.get("owner_deleted")
                else (
                    "conflict"
                    if ledger.get("conflict")
                    else state.get("status", "active")
                )
            )
        )
        return {
            **state,
            "title": document["title"],
            "document": document,
            "items": projection,
            "hash": current_hash,
            "status": status,
            "source_link": "#/knowledge/item/" + source["id"],
            "collection_link": "#/knowledge?collection=" + state["collection_id"],
        }

    def list(self):
        items = []
        for row in self.db.execute(
            "SELECT id FROM capability_knowledge_ideas ORDER BY rowid DESC LIMIT 100"
        ):
            try:
                items.append(self.get(row[0]))
            except CaptureError:
                continue
        return {"items": items, "vault": self.availability()}

    def export(self, identity):
        detail = self.get(identity)
        return {
            "content": render(detail["document"]),
            "filename": identity + ".md",
            "hash": detail["hash"],
        }

    def _apply(self, document, state=None):
        identity = document["id"]
        guid = "idea_list:" + identity
        source_row = self.db.execute(
            "SELECT id FROM items WHERE guid=?", (guid,)
        ).fetchone()
        source_id = (
            source_row[0]
            if source_row
            else self.store.create_typed_item(
                item_type="note",
                title=document["title"],
                content=render(document),
                guid=guid,
                extra={
                    "file_metadata": {
                        "original_at": document["created"],
                        "idea_list_id": identity,
                    }
                },
            )
        )
        if source_id is None:
            source_id = self.db.execute(
                "SELECT id FROM items WHERE guid=?", (guid,)
            ).fetchone()[0]
        marker = "idea-list:" + identity
        collection = self.db.execute(
            "SELECT id FROM collections WHERE query=? AND kind=?", (marker, "manual")
        ).fetchone()
        if collection:
            collection_id = collection[0]
        else:
            try:
                collection_id = self.store.create_collection(
                    name=document["title"][:100] + " · " + identity,
                    kind="manual",
                    query=marker,
                )
            except sqlite3.IntegrityError:
                collection_id = self.db.execute(
                    "SELECT id FROM collections WHERE query=?", (marker,)
                ).fetchone()[0]
        members = []
        for index, content in enumerate(document["ideas"]):
            item_guid = guid + ":" + str(index)
            row = self.db.execute(
                "SELECT id FROM items WHERE guid=?", (item_guid,)
            ).fetchone()
            item_id = (
                row[0]
                if row
                else self.store.create_typed_item(
                    item_type="fleeting",
                    title=f'{document["title"]} · {index + 1}',
                    content=content,
                    guid=item_guid,
                    extra={
                        "file_metadata": {
                            "original_at": document["created"],
                            "idea_list_id": identity,
                            "idea_position": index,
                        }
                    },
                )
            )
            if item_id is None:
                item_id = self.db.execute(
                    "SELECT id FROM items WHERE guid=?", (item_guid,)
                ).fetchone()[0]
            current = self.store.get_item(item_id)
            if current["content"] != content and state is None:
                raise CaptureError(
                    "Pending canonical idea changed; review before retrying", 409
                )
            if current["content"] != content:
                self.store.update_item(item_id, content=content)
            self.store.add_to_collection(collection_id, item_id)
            members.append(item_id)
        for old in (state or {}).get("member_ids", []):
            if old not in members:
                self.store.remove_from_collection(collection_id, old)
        source = self.store.get_item(source_id)
        if (
            source["content"] != render(document)
            or source["title"] != document["title"]
        ):
            self.store.update_item(
                source_id, title=document["title"], content=render(document)
            )
        result = {
            **(state or {}),
            "id": identity,
            "source_id": source_id,
            "collection_id": collection_id,
            "member_ids": members,
            "document_hash": digest(document),
            "member_hash": digest(document["ideas"]),
            "revision": (state or {}).get("revision", 0) + 1,
            "action_id": digest([str(self.home), identity])[:32],
            "status": "active",
            "sync_enabled": (state or {}).get("sync_enabled", False),
            "next_fire_at": (state or {}).get("next_fire_at", ""),
        }
        self.put(result)
        return result

    @serialized
    def import_list(self, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "content",
            "preview_id",
            "expected_hash",
        }:
            raise CaptureError(
                "Idea import requires request_id, content, preview_id and expected_hash"
            )
        request = request_key(body["request_id"])
        result = preview(body["content"])
        if result["preview_id"] != body["preview_id"]:
            raise CaptureError("Idea preview changed", 409)
        identity, payload = result["document"]["id"], packed(body)
        self.db.execute("BEGIN IMMEDIATE")
        try:
            previous = self.db.execute(
                "SELECT * FROM capability_knowledge_idea_requests WHERE request_id=?",
                (request,),
            ).fetchone()
            if previous:
                if previous["payload"] != payload:
                    raise CaptureError("Idea request belongs to different input", 409)
                if previous["receipt"]:
                    self.db.commit()
                    return json.loads(previous["receipt"])
            self.guard.assert_scope()
            row = self.db.execute(
                "SELECT body FROM capability_knowledge_ideas WHERE id=?", (identity,)
            ).fetchone()
            state = json.loads(row[0]) if row else None
            if (
                state is None
                and self.db.execute(
                    "SELECT count(*) FROM capability_knowledge_ideas"
                ).fetchone()[0]
                >= 100
            ):
                raise CaptureError("At most100 idea lists are supported")
            if (
                not previous
                and (self.get(identity)["hash"] if state else "")
                != body["expected_hash"]
            ):
                raise CaptureError("Idea list changed; review again", 409)
            if state and self.get(identity)["status"] in (
                "owner_deleted",
                "collection_deleted",
            ):
                raise CaptureError(
                    "Deleted vault binding requires explicit recovery in canonical settings",
                    409,
                )
            if not previous:
                if self.db.execute(
                    "SELECT 1 FROM capability_knowledge_idea_requests WHERE list_id=? AND receipt IS NULL",
                    (identity,),
                ).fetchone():
                    raise CaptureError(
                        "Previous idea import must be retried first", 409
                    )
                self.db.execute(
                    "INSERT INTO capability_knowledge_idea_requests VALUES (?,?,?,NULL)",
                    (request, payload, identity),
                )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        if (
            not state
            or state["document_hash"] != digest(result["document"])
            or self.get(identity)["document"] != result["document"]
        ):
            self._apply(result["document"], state)
        receipt = self.get(identity)
        self.db.execute(
            "UPDATE capability_knowledge_idea_requests SET receipt=? WHERE request_id=? AND receipt IS NULL",
            (packed(receipt), request),
        )
        self.db.commit()
        return json.loads(
            self.db.execute(
                "SELECT receipt FROM capability_knowledge_idea_requests WHERE request_id=?",
                (request,),
            ).fetchone()[0]
        )

    @serialized
    def sync(self, identity, body):
        if not isinstance(body, dict) or set(body) != {"request_id", "expected_hash"}:
            raise CaptureError("Idea sync requires request_id and expected_hash")
        request = request_key(body["request_id"])
        payload = packed({"operation": "sync", "id": identity, **body})
        prior = self.db.execute(
            "SELECT * FROM capability_knowledge_idea_requests WHERE request_id=?",
            (request,),
        ).fetchone()
        if prior:
            if prior["payload"] != payload:
                raise CaptureError("Idea request belongs to different input", 409)
            if prior["receipt"]:
                return json.loads(prior["receipt"])
        self.guard.assert_scope()
        if not prior:
            if self.get(identity)["hash"] != body["expected_hash"]:
                raise CaptureError("Idea list changed; reload before syncing", 409)
            if self.db.execute(
                "SELECT 1 FROM capability_knowledge_idea_requests WHERE list_id=? AND receipt IS NULL",
                (identity,),
            ).fetchone():
                raise CaptureError("Previous idea operation must be retried first", 409)
            self.db.execute(
                "INSERT INTO capability_knowledge_idea_requests VALUES (?,?,?,NULL)",
                (request, payload, identity),
            )
            self.db.commit()
        result = self._sync(identity, body, resuming=bool(prior))
        self.db.execute(
            "UPDATE capability_knowledge_idea_requests SET receipt=? WHERE request_id=? AND receipt IS NULL",
            (packed(result), request),
        )
        self.db.commit()
        return result

    def _sync(self, identity, body, resuming=False):
        if not isinstance(body, dict) or set(body) != {"request_id", "expected_hash"}:
            raise CaptureError("Idea sync requires request_id and expected_hash")
        request_key(body["request_id"])
        self.guard.assert_scope()
        if (
            not self.availability()["available"]
            or self.vault_root.resolve() != self.vault_root
        ):
            raise CaptureError(
                self.availability()["reason"] or "Owned vault path changed", 409
            )
        detail = self.get(identity)
        if not resuming and detail["hash"] != body["expected_hash"]:
            raise CaptureError("Idea list changed; reload before syncing", 409)
        if detail["status"] in ("owner_deleted", "collection_deleted"):
            return {"outcome": detail["status"], **detail}
        for row in self.store.vault_projections():
            if (
                not (self.vault_root / row["relpath"])
                .resolve()
                .is_relative_to(self.vault_root)
            ):
                raise CaptureError("Vault projection path escapes the owned root", 409)
        state = self.state(identity)
        source = self.store.get_item(state["source_id"])
        original = parse(source["content"])
        local_changed = digest(detail["document"]["ideas"]) != state["member_hash"]
        cover_changed = digest(original) != state["document_hash"]
        if local_changed and cover_changed:
            raise CaptureError(
                "Idea members and their source changed concurrently; review both before importing",
                409,
            )
        if local_changed:
            changed = {
                **detail["document"],
                "modified": datetime.now(timezone.utc).isoformat(),
            }
            self.store.update_item(state["source_id"], content=render(changed))
        outcome = self.vault.sync_batch(max_items=1000)
        ledger = self.store.vault_projection(state["source_id"]) or {}
        if ledger.get("owner_deleted") or ledger.get("conflict"):
            return {
                "outcome": (
                    "owner_deleted" if ledger.get("owner_deleted") else "conflict"
                ),
                **self.get(identity),
            }
        source = self.store.get_item(state["source_id"])
        incoming = parse(source["content"])
        if incoming["id"] != identity:
            raise CaptureError(
                "Vault list identity changed; no collection update applied", 409
            )
        changed = digest(incoming) != state["document_hash"]
        if changed:
            self._apply(incoming, state)
        return {
            "outcome": (
                "imported"
                if changed and not local_changed
                else ("exported" if local_changed else "unchanged")
            ),
            **self.get(identity),
            "vault_result": outcome,
        }
