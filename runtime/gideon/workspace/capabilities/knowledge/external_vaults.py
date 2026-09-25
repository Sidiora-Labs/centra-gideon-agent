"""Explicitly allowed external markdown vaults indexed into canonical Knowledge."""

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from gideon.core.atomic_write import atomic_write

from .capture import CaptureError, CaptureInbox, request_key
from .reviews import packed

SKIP = {".git", ".obsidian", ".trash", "node_modules"}
WIKILINK = re.compile(r"\[\[([^\[\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
TAG = re.compile(r"(?<![\w/])#([A-Za-z][\w/-]{0,99})")


def digest(content):
    return hashlib.sha256(content.encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def note_path(value):
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 1000
        or "\\" in value
        or value.startswith("/")
    ):
        raise CaptureError("Note path must be a relative forward-slash Markdown path")
    if any(part in ("", ".", "..") for part in value.split("/")):
        raise CaptureError("Note path must be a relative forward-slash Markdown path")
    path = PurePosixPath(value)
    if (
        any(part in ("", ".", "..") for part in path.parts)
        or path.suffix.lower() != ".md"
        or any(part.startswith(".") for part in path.parts)
    ):
        raise CaptureError("Note path must be a relative forward-slash Markdown path")
    return path.as_posix()


class ExternalVaults:
    def __init__(self, store, home=None):
        self.store, self.db = store, store.db
        self.home = Path(home).resolve() if home else CaptureInbox._runtime_home()
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS capability_knowledge_vault_notes(source_id TEXT NOT NULL,path TEXT NOT NULL,item_id TEXT NOT NULL,content_hash TEXT NOT NULL,size INTEGER NOT NULL,modified_ns INTEGER NOT NULL,tags TEXT NOT NULL,wikilinks TEXT NOT NULL,indexed_at TEXT NOT NULL,PRIMARY KEY(source_id,path),UNIQUE(source_id,item_id));
        CREATE TABLE IF NOT EXISTS capability_knowledge_vault_requests(request_id TEXT PRIMARY KEY,payload TEXT NOT NULL,receipt TEXT NOT NULL);
        """)
        self.db.commit()

    def assert_scope(self):
        if CaptureInbox._runtime_home() != self.home:
            raise CaptureError(
                "Runtime home changed; restore the bound allocation", 409
            )

    def allowed_roots(self):
        try:
            raw = (
                json.loads((self.home / "config.json").read_text())
                .get("knowledge", {})
                .get("external_vault_roots", [])
            )
        except (OSError, ValueError, TypeError):
            raw = []
        if not isinstance(raw, list):
            raw = []
        roots = []
        for value in raw:
            try:
                root = Path(str(value)).expanduser().resolve(strict=True)
                if root.is_dir() and root not in roots:
                    roots.append(root)
            except OSError:
                continue
        return roots

    def _registered_path(self, value):
        if not isinstance(value, str) or not value or len(value) > 1000:
            raise CaptureError("Vault path is required")
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except OSError:
            raise CaptureError("Vault directory does not exist", 404) from None
        if not path.is_dir() or not any(
            path == root or path.is_relative_to(root) for root in self.allowed_roots()
        ):
            raise CaptureError(
                "Vault directory is outside configured external_vault_roots"
            )
        return path

    def _source(self, identity):
        source = self.store.get_source(identity)
        if (
            not source
            or source.get("provider") != "external_vault"
            or source.get("kind") != "filesystem"
        ):
            raise CaptureError("External vault not found", 404)
        return source

    def _root(self, source):
        path = self._registered_path(source["spec"].get("path", ""))
        if str(path) != source["spec"].get("path"):
            raise CaptureError("Vault root identity changed after registration", 409)
        return path

    def _target(self, source, relative, existing=True):
        relative = note_path(relative)
        root = self._root(source)
        candidate = root.joinpath(*PurePosixPath(relative).parts)
        if candidate.is_symlink():
            raise CaptureError("Symlink note targets are not allowed", 403)
        try:
            target = candidate.resolve(strict=existing)
            if not existing:
                parent = candidate.parent.resolve(strict=True)
                target = parent / candidate.name
        except OSError:
            raise CaptureError(
                "Note path or parent directory does not exist", 404
            ) from None
        if target == root or not target.is_relative_to(root):
            raise CaptureError("Note path escapes the registered vault", 403)
        return target, relative

    def list(self):
        items = []
        for source in self.store.list_sources():
            if (
                source.get("provider") == "external_vault"
                and source.get("kind") == "filesystem"
            ):
                count = self.db.execute(
                    "SELECT count(*) FROM capability_knowledge_vault_notes WHERE source_id=?",
                    (source["id"],),
                ).fetchone()[0]
                items.append({**source, "note_count": count})
        return {
            "items": items,
            "allowed_roots": [str(root) for root in self.allowed_roots()],
        }

    def register(self, body):
        if (
            not isinstance(body, dict)
            or set(body) != {"name", "path"}
            or not isinstance(body["name"], str)
            or not body["name"].strip()
            or len(body["name"]) > 200
        ):
            raise CaptureError("Vault registration requires name and path")
        self.assert_scope()
        path = self._registered_path(body["path"])
        for source in self.list()["items"]:
            if source["spec"].get("path") == str(path):
                raise CaptureError("Vault path is already registered", 409)
        identity = self.store.create_source(
            name=body["name"].strip(),
            provider="external_vault",
            kind="filesystem",
            spec={"path": str(path)},
            enrichment="none",
            poll_interval_secs=86400,
            item_type="note",
            enabled=True,
            created_by="user",
        )
        return self._source(identity)

    def remove(self, identity):
        source = self._source(identity)
        self.assert_scope()
        self.store.update_source(identity, enabled=False)
        self.db.execute("UPDATE items SET is_archived=1 WHERE source_id=?", (identity,))
        self.db.commit()
        return {
            "id": identity,
            "enabled": False,
            "preserved_path": source["spec"]["path"],
            "preserved_items": self.db.execute(
                "SELECT count(*) FROM capability_knowledge_vault_notes WHERE source_id=?",
                (identity,),
            ).fetchone()[0],
        }

    def _metadata(self, content):
        return sorted(set(TAG.findall(content))), sorted(
            {match.strip() for match in WIKILINK.findall(content) if match.strip()}
        )

    def _index(self, source, root, path):
        relative = path.relative_to(root).as_posix()
        content = path.read_text(errors="replace")
        if len(content.encode()) > 1048576:
            raise CaptureError("Markdown note exceeds1MiB", 413)
        content_hash = digest(content)
        tags, links = self._metadata(content)
        title = path.stem.replace("-", " ").strip() or path.stem
        row = self.db.execute(
            "SELECT item_id FROM capability_knowledge_vault_notes WHERE source_id=? AND path=?",
            (source["id"], relative),
        ).fetchone()
        metadata = {
            "external_vault_id": source["id"],
            "external_vault_path": relative,
            "content_hash": content_hash,
            "wikilinks": links,
            "external_deleted": False,
        }
        if row:
            item_id = row[0]
            self.store.update_item(
                item_id,
                title=title,
                content=content,
                tags=tags,
                is_archived=False,
                file_metadata=metadata,
            )
        else:
            guid = "external-vault:" + relative
            item_id = self.store.create_typed_item(
                item_type="note",
                title=title,
                content=content,
                tags=tags,
                provider="external_vault",
                source_id=source["id"],
                guid=guid,
                extra={"file_metadata": metadata},
            )
            if item_id is None:
                item_id = self.db.execute(
                    "SELECT id FROM items WHERE source_id=? AND guid=?",
                    (source["id"], guid),
                ).fetchone()[0]
        stat = path.stat()
        self.db.execute(
            "INSERT INTO capability_knowledge_vault_notes VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(source_id,path) DO UPDATE SET item_id=excluded.item_id,content_hash=excluded.content_hash,size=excluded.size,modified_ns=excluded.modified_ns,tags=excluded.tags,wikilinks=excluded.wikilinks,indexed_at=excluded.indexed_at",
            (
                source["id"],
                relative,
                item_id,
                content_hash,
                stat.st_size,
                stat.st_mtime_ns,
                packed(tags),
                packed(links),
                now(),
            ),
        )
        return self.detail(source["id"], relative)

    def scan(self, identity):
        source = self._source(identity)
        self.assert_scope()
        root = self._root(source)
        paths = []
        for current, directories, files in os.walk(root, followlinks=False):
            current_path = Path(current)
            directories[:] = [
                name
                for name in directories
                if name not in SKIP
                and not name.startswith(".")
                and not (current_path / name).is_symlink()
            ]
            for name in files:
                path = current_path / name
                if (
                    name.startswith(".")
                    or path.suffix.lower() != ".md"
                    or path.is_symlink()
                ):
                    continue
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root):
                    continue
                paths.append(resolved)
        if len(paths) > 5000:
            raise CaptureError("Vault exceeds5000 Markdown notes", 413)
        total = sum(path.stat().st_size for path in paths)
        if total > 67108864:
            raise CaptureError("Vault Markdown exceeds64MiB", 413)
        seen = set()
        for path in sorted(paths):
            seen.add(path.relative_to(root).as_posix())
            self._index(source, root, path)
        missing = self.db.execute(
            "SELECT path,item_id FROM capability_knowledge_vault_notes WHERE source_id=?",
            (identity,),
        ).fetchall()
        deleted = 0
        for row in missing:
            if row["path"] not in seen:
                item = self.store.get_item(row["item_id"])
                metadata = dict(item.get("file_metadata") or {}) if item else {}
                metadata["external_deleted"] = True
                if item:
                    self.store.update_item(
                        row["item_id"], is_archived=True, file_metadata=metadata
                    )
                self.db.execute(
                    "DELETE FROM capability_knowledge_vault_notes WHERE source_id=? AND path=?",
                    (identity, row["path"]),
                )
                deleted += 1
        self.db.commit()
        self.store.record_poll(
            identity,
            cursor=digest("\n".join(sorted(seen))),
            new_count=len(seen),
            health_status="ok",
        )
        return {
            "vault": self._source(identity),
            "notes": self.notes(identity),
            "total": len(seen),
            "deleted_refs": deleted,
        }

    def notes(self, identity):
        self._source(identity)
        return [
            self.detail(identity, row[0])
            for row in self.db.execute(
                "SELECT path FROM capability_knowledge_vault_notes WHERE source_id=? ORDER BY path",
                (identity,),
            )
        ]

    def detail(self, identity, relative):
        source = self._source(identity)
        target, relative = self._target(source, relative)
        row = self.db.execute(
            "SELECT * FROM capability_knowledge_vault_notes WHERE source_id=? AND path=?",
            (identity, relative),
        ).fetchone()
        if not row:
            raise CaptureError("Indexed vault note not found", 404)
        content = target.read_text(errors="replace")
        item = self.store.get_item(row["item_id"])
        return {
            **dict(row),
            "tags": json.loads(row["tags"]),
            "wikilinks": json.loads(row["wikilinks"]),
            "content": content,
            "current_hash": digest(content),
            "source_link": "#/knowledge/item/" + row["item_id"],
            "title": item["title"] if item else Path(relative).stem,
        }

    def _receipt(self, request, payload):
        row = self.db.execute(
            "SELECT payload,receipt FROM capability_knowledge_vault_requests WHERE request_id=?",
            (request,),
        ).fetchone()
        if row and row["payload"] != payload:
            raise CaptureError("request_id belongs to a different vault mutation", 409)
        return json.loads(row["receipt"]) if row else None

    def write(self, identity, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "path",
            "content",
            "expected_hash",
        }:
            raise CaptureError(
                "Vault write requires request_id, path, content and expected_hash"
            )
        if (
            not isinstance(body["content"], str)
            or len(body["content"].encode()) > 1048576
        ):
            raise CaptureError("Vault note content exceeds1MiB")
        request, payload = request_key(body["request_id"]), packed(body)
        prior = self._receipt(request, payload)
        if prior:
            return prior
        source = self._source(identity)
        self.assert_scope()
        try:
            target, relative = self._target(source, body["path"])
            current = target.read_text(errors="replace")
            exists = True
        except CaptureError as exc:
            if exc.status != 404:
                raise
            target, relative = self._target(source, body["path"], existing=False)
            current = ""
            exists = False
        current_hash = digest(current) if exists else ""
        if current_hash != body["expected_hash"]:
            raise CaptureError("Vault note changed; reload before saving", 409)
        atomic_write(target, body["content"], fsync=True)
        result = {
            **self._index(source, self._root(source), target),
            "created": not exists,
        }
        self.db.execute(
            "INSERT INTO capability_knowledge_vault_requests VALUES (?,?,?)",
            (request, payload, packed(result)),
        )
        self.db.commit()
        return result

    def delete(self, identity, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "path",
            "expected_hash",
        }:
            raise CaptureError(
                "Vault delete requires request_id, path and expected_hash"
            )
        request, payload = request_key(body["request_id"]), packed(body)
        prior = self._receipt(request, payload)
        if prior:
            return prior
        source = self._source(identity)
        self.assert_scope()
        target, relative = self._target(source, body["path"])
        content = target.read_text(errors="replace")
        if digest(content) != body["expected_hash"]:
            raise CaptureError("Vault note changed; reload before deleting", 409)
        row = self.db.execute(
            "SELECT item_id FROM capability_knowledge_vault_notes WHERE source_id=? AND path=?",
            (identity, relative),
        ).fetchone()
        target.unlink()
        if row:
            item = self.store.get_item(row[0])
            metadata = dict(item.get("file_metadata") or {}) if item else {}
            metadata["external_deleted"] = True
            if item:
                self.store.update_item(row[0], is_archived=True, file_metadata=metadata)
        self.db.execute(
            "DELETE FROM capability_knowledge_vault_notes WHERE source_id=? AND path=?",
            (identity, relative),
        )
        result = {
            "deleted": relative,
            "item_id": row[0] if row else None,
            "file_recreated": False,
        }
        self.db.execute(
            "INSERT INTO capability_knowledge_vault_requests VALUES (?,?,?)",
            (request, payload, packed(result)),
        )
        self.db.commit()
        return result

    def search(self, identity, query):
        if not isinstance(query, str) or not 1 <= len(query) <= 500:
            raise CaptureError("Search query is required")
        value = query.casefold()
        return {
            "results": [
                {
                    key: note[key]
                    for key in ("path", "title", "tags", "current_hash", "source_link")
                }
                for note in self.notes(identity)
                if value in note["content"].casefold()
                or value in note["path"].casefold()
            ]
        }

    def graph(self, identity):
        notes = self.notes(identity)
        names = {}
        for note in notes:
            names.setdefault(Path(note["path"]).stem.casefold(), note["path"])
        edges = []
        for note in notes:
            for link in note["wikilinks"]:
                target = names.get(Path(link).stem.casefold())
                edges.append(
                    {
                        "source": note["path"],
                        "target": target or link,
                        "resolved": bool(target),
                    }
                )
        return {
            "nodes": [
                {
                    "id": note["path"],
                    "title": note["title"],
                    "source_link": note["source_link"],
                }
                for note in notes
            ],
            "edges": edges,
        }
