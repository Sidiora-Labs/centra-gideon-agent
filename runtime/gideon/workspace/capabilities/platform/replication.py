"""Signed direct-peer replication over canonical durability entries."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from gideon.core.config.loader import config_dir
from gideon.operations.durability import conflicts, inventory, reconcile, tombstones
from gideon.operations.durability.shards import canonical_json
from gideon.workspace.capabilities.platform.peers import PeerError, PeerStore
from gideon.workspace.capabilities.platform import replication_adapters
from gideon.workspace.capabilities.platform import replication_commissions
from gideon.workspace.capabilities.platform import replication_creative_direction
from gideon.workspace.capabilities.platform import replication_experience_stories
from gideon.workspace.capabilities.platform import replication_identity_stories
from gideon.workspace.capabilities.platform import replication_knowledge_collections
from gideon.workspace.capabilities.platform import replication_music_video
from gideon.workspace.capabilities.platform import replication_video

SCHEMA_VERSION = 1
RECEIVE_PATH = "/api/capabilities/platform/replication/receive"


@dataclass(frozen=True)
class Domain:
    scope: str
    entries: tuple[str, ...]


DOMAINS = {
    "workspace.records": Domain("workspace.records", ("projects", "tasks")),
    "knowledge.records": Domain("knowledge.records", ("knowledge.items",)),
    replication_knowledge_collections.SCOPE: Domain(replication_knowledge_collections.SCOPE, replication_knowledge_collections.ENTRIES),
    "creative.catalog": Domain("creative.catalog", tuple(replication_adapters.CREATIVE_TABLES)),
    replication_commissions.SCOPE: Domain(replication_commissions.SCOPE, replication_commissions.ENTRIES),
    replication_creative_direction.SCOPE: Domain(replication_creative_direction.SCOPE, replication_creative_direction.ENTRIES),
    "identity.goals": Domain("identity.goals", tuple(replication_adapters.IDENTITY_TABLES)),
    "identity.profile": Domain("identity.profile", ("identity.progress_profile", "identity.twin_profile", "identity.twin_documents")),
    replication_identity_stories.SCOPE: Domain(replication_identity_stories.SCOPE, replication_identity_stories.ENTRIES),
    replication_experience_stories.SCOPE: Domain(replication_experience_stories.SCOPE, replication_experience_stories.ENTRIES),
    "communications.contacts": Domain("communications.contacts", tuple(replication_adapters.COMMUNICATION_TABLES)),
    "music.library": Domain("music.library", replication_adapters.MUSIC_ENTRIES),
    "media.assets": Domain("media.assets", replication_adapters.MEDIA_ENTRIES),
    replication_video.SCOPE: Domain(replication_video.SCOPE, replication_video.ENTRIES),
    replication_music_video.SCOPE: Domain(replication_music_video.SCOPE, replication_music_video.ENTRIES),
    "wellbeing.health": Domain("wellbeing.health", replication_adapters.WELLBEING_HEALTH_ENTRIES),
    "wellbeing.routines": Domain("wellbeing.routines", replication_adapters.WELLBEING_ROUTINE_ENTRIES),
    "wellbeing.genome": Domain("wellbeing.genome", replication_adapters.WELLBEING_GENOME_ENTRIES),
    "wellbeing.practice": Domain("wellbeing.practice", replication_adapters.WELLBEING_PRACTICE_ENTRIES),
    "wellbeing.life_calendar": Domain("wellbeing.life_calendar", replication_adapters.WELLBEING_CALENDAR_ENTRIES),
}
_INVENTORY = {entry.id: entry for entry in inventory.INVENTORY}
_DOMAIN_MERGES = {"projects": inventory.MERGE_LWW, "tasks": inventory.MERGE_LWW}


class ReplicationError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


class ReplicationService:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or config_dir())
        self.path = self.home / "capabilities/platform/replication.sqlite3"
        self.peers = PeerStore(self.home)

    def _connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS outbound(domain TEXT PRIMARY KEY,sequence INTEGER NOT NULL,fingerprint TEXT NOT NULL,batch TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS cursors(peer_id TEXT NOT NULL,domain TEXT NOT NULL,sequence INTEGER NOT NULL,fingerprint TEXT NOT NULL,response TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(peer_id,domain));
            CREATE TABLE IF NOT EXISTS ancestors(peer_id TEXT NOT NULL,entry_id TEXT NOT NULL,entity_id TEXT NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(peer_id,entry_id,entity_id));
        """)
        return connection

    @staticmethod
    def _domain(scope: str) -> Domain:
        try:
            return DOMAINS[scope]
        except (KeyError, TypeError):
            raise ReplicationError("Unsupported replication domain", 404)

    @staticmethod
    def _entry(entry_id: str):
        entry = _INVENTORY.get(entry_id)
        if entry is None or entry.secret or entry.derived or not reconcile.handles_kind(entry.kind):
            raise ReplicationError("Replication entry is not an exportable canonical row store", 400)
        return replace(entry, merge=_DOMAIN_MERGES.get(entry_id, entry.merge))

    def _rows(self, entry_id: str) -> list[dict]:
        if entry_id in replication_identity_stories.ENTRIES:
            return replication_identity_stories.read_rows(self.home, entry_id)
        if entry_id in replication_knowledge_collections.ENTRIES:
            return replication_knowledge_collections.read_rows(self.home, entry_id)
        if entry_id in replication_experience_stories.ENTRIES:
            return replication_experience_stories.read_rows(self.home, entry_id)
        if entry_id in replication_commissions.ENTRIES:
            return replication_commissions.read_rows(self.home, entry_id)
        if entry_id in replication_creative_direction.ENTRIES:
            return replication_creative_direction.read_rows(self.home, entry_id)
        if entry_id in replication_video.ENTRIES:
            return replication_video.read_rows(self.home, entry_id)
        if entry_id in replication_music_video.ENTRIES:
            return replication_music_video.read_rows(self.home, entry_id)
        if entry_id in replication_adapters.SQLITE_ENTRIES:
            return replication_adapters.read_rows(self.home, entry_id)
        entry = self._entry(entry_id)
        rows = reconcile.read_local_rows(entry, self.home / entry.path)
        if entry.tombstones:
            rows = tombstones.merge_into_rows(self.home / entry.path, rows)
        return rows

    def export_batch(self, peer_id: str, scope: str) -> dict:
        domain = self._domain(scope)
        if not self.peers.allows(peer_id, scope, "send"):
            raise ReplicationError("Peer policy denies this replication export", 403)
        identity = self.peers.snapshot()["self"]
        entries = [{"entry_id": entry_id, "rows": self._rows(entry_id)} for entry_id in domain.entries]
        content = {"schema_version": SCHEMA_VERSION, "domain": scope, "sender": identity["peer_id"], "entries": entries}
        fingerprint = hashlib.sha256(canonical_json(content).encode()).hexdigest()
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM outbound WHERE domain=?", (scope,)).fetchone()
            if row and row["fingerprint"] == fingerprint:
                return json.loads(row["batch"])
            sequence = (row["sequence"] if row else 0) + 1
            batch_id = hashlib.sha256(f"{identity['peer_id']}|{scope}|{sequence}|{fingerprint}".encode()).hexdigest()[:32]
            batch = {**content, "sequence": sequence, "batch_id": batch_id}
            connection.execute("INSERT INTO outbound VALUES(?,?,?,?) ON CONFLICT(domain) DO UPDATE SET sequence=excluded.sequence,fingerprint=excluded.fingerprint,batch=excluded.batch", (scope, sequence, fingerprint, canonical_json(batch)))
        return batch

    def _ancestors(self, connection, peer_id: str, entry_id: str) -> dict[str, str]:
        return {row["entity_id"]: row["sha"] for row in connection.execute("SELECT entity_id,sha FROM ancestors WHERE peer_id=? AND entry_id=?", (peer_id, entry_id))}

    @staticmethod
    def _fingerprint(batch: dict) -> str:
        return hashlib.sha256(canonical_json(batch).encode()).hexdigest()

    def apply_batch(self, peer_id: str, batch: dict) -> dict:
        fields = {"schema_version", "domain", "sender", "sequence", "batch_id", "entries"}
        if not isinstance(batch, dict) or set(batch) != fields:
            raise ReplicationError("Invalid replication batch")
        if batch["schema_version"] != SCHEMA_VERSION:
            raise ReplicationError("Unsupported replication schema version", 409)
        scope, sequence = batch["domain"], batch["sequence"]
        domain = self._domain(scope)
        if batch["sender"] != peer_id:
            raise ReplicationError("Replication sender does not match verified peer", 403)
        if not self.peers.allows(peer_id, scope, "receive"):
            raise ReplicationError("Peer policy denies this replication import", 403)
        if type(sequence) is not int or sequence < 1 or not isinstance(batch["batch_id"], str) or len(batch["batch_id"]) != 32:
            raise ReplicationError("Invalid replication sequence or batch ID")
        entries = batch["entries"]
        if not isinstance(entries, list) or [item.get("entry_id") if isinstance(item, dict) else None for item in entries] != list(domain.entries):
            raise ReplicationError("Replication coverage does not match the declared domain")
        if any(set(item) != {"entry_id", "rows"} or not isinstance(item["rows"], list) or any(not isinstance(row, dict) for row in item["rows"]) for item in entries):
            raise ReplicationError("Invalid replication rows")
        try:
            replication_adapters.validate_entries(scope, entries)
            if scope == replication_identity_stories.SCOPE:
                replication_identity_stories.validate_entries(entries)
            if scope == replication_knowledge_collections.SCOPE:
                replication_knowledge_collections.validate_entries(entries, self.home)
            if scope == replication_experience_stories.SCOPE:
                replication_experience_stories.validate_entries(entries)
            if scope == replication_commissions.SCOPE:
                replication_commissions.validate_entries(entries)
            if scope == replication_creative_direction.SCOPE:
                replication_creative_direction.validate_entries(entries)
            if scope == replication_video.SCOPE:
                replication_video.validate_entries(entries)
            if scope == replication_music_video.SCOPE:
                replication_music_video.validate_entries(entries)
            if scope == "music.library":
                replication_adapters.validate_music_entries(entries)
            if scope.startswith("wellbeing."):
                replication_adapters.validate_wellbeing_entries(scope, entries)
        except ValueError as error:
            raise ReplicationError(str(error), 422) from error
        fingerprint = self._fingerprint(batch)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute("SELECT * FROM cursors WHERE peer_id=? AND domain=?", (peer_id, scope)).fetchone()
            if cursor and sequence == cursor["sequence"]:
                if fingerprint != cursor["fingerprint"]:
                    raise ReplicationError("Replication sequence was reused for different content", 409)
                return json.loads(cursor["response"])
            expected = (cursor["sequence"] if cursor else 0) + 1
            if sequence != expected:
                raise ReplicationError(f"Replication cursor expects sequence {expected}", 409)
            queue = conflicts.ConflictQueue(self.home)
            results, ancestor_updates = [], []
            if scope == replication_knowledge_collections.SCOPE:
                try:
                    applied = replication_knowledge_collections.apply_entries(
                        self.home, entries,
                        {entry_id: self._ancestors(connection, peer_id, entry_id) for entry_id in domain.entries},
                        queue, now,
                    )
                except ValueError as error:
                    raise ReplicationError(str(error), 422) from error
                applied_items = [(item, applied[item["entry_id"]]) for item in entries]
            else:
                applied_items = []
            pending_entries = () if scope == replication_knowledge_collections.SCOPE else entries
            for item in pending_entries:
                entry_id = item["entry_id"]
                if entry_id in replication_identity_stories.ENTRIES:
                    try:
                        result = replication_identity_stories.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                elif entry_id in replication_experience_stories.ENTRIES:
                    try:
                        result = replication_experience_stories.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                elif entry_id in replication_commissions.ENTRIES:
                    try:
                        result = replication_commissions.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                elif entry_id in replication_creative_direction.ENTRIES:
                    try:
                        result = replication_creative_direction.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                elif entry_id in replication_video.ENTRIES:
                    try:
                        result = replication_video.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                elif entry_id in replication_music_video.ENTRIES:
                    try:
                        result = replication_music_video.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                elif entry_id in replication_adapters.SQLITE_ENTRIES:
                    try:
                        result = replication_adapters.apply_rows(self.home, entry_id, item["rows"], self._ancestors(connection, peer_id, entry_id), queue, now)
                    except ValueError as error:
                        raise ReplicationError(str(error), 422) from error
                else:
                    entry = self._entry(entry_id)
                    result = replication_adapters.apply_inventory_rows(self.home, entry, item["rows"], self._ancestors(connection, peer_id, entry.id), queue, now)
                applied_items.append((item, result))
            for item, result in applied_items:
                entry_id = item["entry_id"]
                if result.verdict != "consumed":
                    raise ReplicationError(f"Replication payload for {entry_id} was rejected", 422)
                results.append({"entry_id": entry_id, "added": result.added, "updated": result.updated, "removed": result.removed, "conflicts": result.conflicts})
                ancestor_updates.extend((entry_id, entity_id, sha) for entity_id, sha in result.new_ancestors.items())
            for entry_id, entity_id, sha in ancestor_updates:
                connection.execute("INSERT INTO ancestors VALUES(?,?,?,?) ON CONFLICT(peer_id,entry_id,entity_id) DO UPDATE SET sha=excluded.sha", (peer_id, entry_id, entity_id, sha))
            response = {"accepted": True, "domain": scope, "sequence": sequence, "batch_id": batch["batch_id"], "entries": results}
            packed = canonical_json(response)
            connection.execute("INSERT INTO cursors VALUES(?,?,?,?,?,?) ON CONFLICT(peer_id,domain) DO UPDATE SET sequence=excluded.sequence,fingerprint=excluded.fingerprint,response=excluded.response,updated_at=excluded.updated_at", (peer_id, scope, sequence, fingerprint, packed, now))
        return response

    def _record_sent(self, peer_id: str, batch: dict) -> None:
        with self._connect() as connection:
            for item in batch["entries"]:
                for row in item["rows"]:
                    entity_id = conflicts.row_id(row)
                    if entity_id:
                        connection.execute("INSERT INTO ancestors VALUES(?,?,?,?) ON CONFLICT(peer_id,entry_id,entity_id) DO UPDATE SET sha=excluded.sha", (peer_id, item["entry_id"], entity_id, conflicts.row_sha(row)))

    async def push(self, peer_id: str, scope: str) -> dict:
        batch = self.export_batch(peer_id, scope)
        response = await self.peers.post_signed(peer_id, scope, RECEIVE_PATH, batch)
        if not isinstance(response, dict) or response.get("accepted") is not True or response.get("batch_id") != batch["batch_id"]:
            raise ReplicationError("Peer did not acknowledge the replication batch", 502)
        self._record_sent(peer_id, batch)
        return response

    def restore_fields(self, conflict_id: str, fields: list[str]) -> dict:
        try:
            record = conflicts.ConflictQueue(self.home).get(conflict_id)
            if record is not None and record.entry_id in replication_identity_stories.ENTRIES:
                return replication_identity_stories.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            if record is not None and record.entry_id in replication_knowledge_collections.ENTRIES:
                return replication_knowledge_collections.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            if record is not None and record.entry_id in replication_experience_stories.ENTRIES:
                return replication_experience_stories.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            if record is not None and record.entry_id in replication_commissions.ENTRIES:
                return replication_commissions.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            if record is not None and record.entry_id in replication_creative_direction.ENTRIES:
                return replication_creative_direction.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            if record is not None and record.entry_id in replication_video.ENTRIES:
                return replication_video.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            if record is not None and record.entry_id in replication_music_video.ENTRIES:
                return replication_music_video.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
            return replication_adapters.restore_fields(self.home, conflict_id, fields, datetime.now(timezone.utc).isoformat())
        except ValueError as error:
            raise ReplicationError(str(error), 409) from error

    def status(self) -> dict:
        with self._connect() as connection:
            cursors = [dict(row) for row in connection.execute("SELECT peer_id,domain,sequence,updated_at FROM cursors ORDER BY peer_id,domain")]
        pending = [row.to_dict() for row in conflicts.ConflictQueue(self.home).items(status=conflicts.STATUS_NEEDS_REVIEW)]
        return {"version": 1, "domains": [{"scope": scope, "entries": list(domain.entries)} for scope, domain in DOMAINS.items()], "cursors": cursors, "conflicts": pending, "peers": self.peers.snapshot()["peers"]}
