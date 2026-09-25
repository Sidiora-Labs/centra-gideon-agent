"""Scheduled-turn control and existing bounded memory slot integration."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from gideon.core.config import config_dir
from gideon.cognition import memory_slots
from gideon.cognition.vector_memory import SemanticArchive
from gideon.workspace.capabilities.identity.store import ConflictError

SLOTS = ("persona", "self_notes")


class ContinuityStore:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.path = self.home / "capabilities/identity/continuity.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript("CREATE TABLE IF NOT EXISTS policy(id INTEGER PRIMARY KEY CHECK(id=1),body TEXT NOT NULL);"
                             "CREATE TABLE IF NOT EXISTS journal(sequence INTEGER PRIMARY KEY AUTOINCREMENT,request_id TEXT UNIQUE,fingerprint TEXT,body TEXT NOT NULL);")

    def policy(self):
        with sqlite3.connect(self.path) as db:
            row = db.execute("SELECT body FROM policy WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {"revision": 0, "heartbeat_paused": False}

    def configure(self, *, heartbeat_paused, expected_revision, request_id):
        if type(heartbeat_paused) is not bool or type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("Expected heartbeat_paused boolean and nonnegative revision")
        if not isinstance(request_id, str) or not 1 <= len(request_id.strip()) <= 128:
            raise ValueError("request_id is required")
        fingerprint = json.dumps([heartbeat_paused, expected_revision])
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute("SELECT fingerprint,body FROM journal WHERE request_id=?", (request_id,)).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError("request_id already names another continuity change")
                return json.loads(prior[1])["policy"]
            row = db.execute("SELECT body FROM policy WHERE id=1").fetchone()
            revision = json.loads(row[0])["revision"] if row else 0
            if revision != expected_revision:
                raise ConflictError("Continuity policy changed; reload before saving")
            policy = {"revision": revision + 1, "heartbeat_paused": heartbeat_paused}
            entry = {"policy": policy, "action": "pause" if heartbeat_paused else "resume", "created_at": datetime.now(timezone.utc).isoformat()}
            db.execute("INSERT OR REPLACE INTO policy VALUES(1,?)", (json.dumps(policy),))
            db.execute("INSERT INTO journal(request_id,fingerprint,body) VALUES(?,?,?)", (request_id, fingerprint, json.dumps(entry)))
            return policy

    @contextmanager
    def _memory(self):
        store = SemanticArchive(self.home / "memory.db")
        store.init()
        try:
            yield store
        finally:
            store.close()

    @staticmethod
    def _validate(slot, text):
        if slot not in SLOTS or not isinstance(text, str) or not text.strip():
            raise ValueError("Choose persona or self_notes and nonempty text")
        if len(text) > memory_slots.cap_for(slot):
            raise ValueError("Anchor exceeds the existing slot character limit")

    def append_anchor(self, *, slot, text, source="user_explicit"):
        self._validate(slot, text)
        if source not in ("user_explicit", "agent_explicit"):
            raise ValueError("Invalid anchor provenance")
        with self._memory() as memory:
            try:
                rows = memory_slots.append(memory, slot, text, source=source)
            except memory_slots.SlotCapExceeded:
                raise ValueError("Slot is full; explicitly remove an existing line first") from None
        return {"slot": slot, "lines": [line.to_dict() for line in rows]}

    def remove_anchor(self, *, slot, text):
        self._validate(slot, text)
        with self._memory() as memory:
            memory_slots.tombstone(memory, slot, text, actor="human", source="user_explicit")
            return {"slot": slot, "lines": [line.to_dict() for line in memory_slots.load(memory, slot)]}

    def status(self):
        with sqlite3.connect(self.path) as db:
            journal = [{"sequence": row[0], **json.loads(row[1])} for row in db.execute("SELECT sequence,body FROM journal ORDER BY sequence")]
        slots, context, events = {name: [] for name in SLOTS}, "", []
        if (self.home / "memory.db").exists():
            with self._memory() as memory:
                slots = {name: [line.to_dict() for line in memory_slots.load(memory, name)] for name in SLOTS}
                context = memory_slots.render_slots_block(memory, names=list(SLOTS))
                events = [row for row in memory.get_events(limit=100) if row.get("memory_key") in {"slot.persona", "slot.self_notes"}]
        return {**self.policy(), "journal": journal, "slots": slots, "context": context, "memory_events": events,
                "scope": "Scheduled heartbeat turns only; active turns, workflows and maintenance are unchanged", "provider_readiness": "unknown"}


def heartbeat_turns_paused(home: Path | None = None):
    home = Path(home) if home is not None else config_dir()
    if not (home / "capabilities/identity/continuity.sqlite3").exists():
        return False
    return ContinuityStore(home).policy()["heartbeat_paused"]
