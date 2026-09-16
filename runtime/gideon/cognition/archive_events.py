"""Durable archive audit rows and the inverse mutations they describe."""

import json
import time


def contract():
    from gideon.cognition import vector_memory

    return vector_memory


class ArchiveJournal:
    def __init__(self, archive):
        self.archive = archive
        self.api = contract()

    def append(self, event_type, memory_type, key, old_value, new_value, source):
        fields = (event_type, memory_type, key, old_value, new_value, source)
        try:
            self.archive.db.execute(
                "INSERT INTO memory_events "
                "(event_type, memory_type, memory_key, old_value, new_value, source, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (*fields, self.api._now_iso()),
            )
            self.archive.db.commit()
        except Exception:
            self.api.logger.debug("Failed to log memory event", exc_info=True)
        self.notify(event_type, key, new_value)

    def notify(self, event_type, key, value):
        try:
            from gideon.automation.event_triggers import SOURCE_MEMORY, emit_event

            emit_event(
                source=SOURCE_MEMORY,
                event_type=event_type,
                key=key,
                value=value,
                now=time.time(),
            )
        except Exception:
            self.api.logger.debug("event-trigger emit failed", exc_info=True)

    def page(self, limit, offset):
        cursor = self.archive.db.execute(
            "SELECT * FROM memory_events ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return list(map(dict, cursor.fetchall()))

    def trim(self, maximum):
        total = self.archive.db.execute(
            "SELECT COUNT(*) FROM memory_events"
        ).fetchone()[0]
        excess = total - maximum
        if excess <= 0:
            return 0
        self.archive.db.execute(
            "DELETE FROM memory_events WHERE id IN "
            "(SELECT id FROM memory_events ORDER BY id ASC LIMIT ?)",
            (excess,),
        )
        self.archive.db.commit()
        return excess

    def undo(self, event_id):
        row = self.archive.db.execute(
            "SELECT * FROM memory_events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return False, f"event {event_id} not found"
        event = dict(row)
        if event.get("undone_at"):
            return True, "already undone"
        kind = event.get("memory_type")
        if kind == "link":
            return self.archive._undo_link_event(event, event_id)
        if kind != "semantic":
            return False, f"{kind} events are not reversible"
        return SemanticInverse(self.archive, event, event_id).apply()

    def mark_reversed(self, event_id, timestamp):
        self.archive.db.execute(
            "UPDATE memory_events SET undone_at = ? WHERE id = ?", (timestamp, event_id)
        )
        self.archive.db.commit()


class SemanticInverse:
    _assignments = {
        "create": ("is_deleted = 1",),
        "promotion": ("is_deleted = 1",),
        "delete": ("is_deleted = 0",),
        "supersede": (
            "is_deleted = 0",
            "superseded_by = NULL",
            "invalidated_at = NULL",
        ),
    }

    def __init__(self, archive, event, event_id):
        self.archive, self.event, self.event_id = archive, event, event_id
        self.api = contract()

    def apply(self):
        event = self.event
        operation, key = event["event_type"], event["memory_key"]
        timestamp = self.api._now_iso()
        values = []
        if operation == "update":
            previous = event.get("old_value")
            if previous is None:
                return False, "update event has no prior value to restore"
            fields = ["value_json = ?", "is_deleted = 0"]
            values.append(previous)
        elif operation in self._assignments:
            fields = list(self._assignments[operation])
        else:
            return False, f"event type {operation!r} is not reversible"
        fields.append("updated_at = ?")
        self.archive.db.execute(
            "UPDATE semantic_memory SET " + ", ".join(fields) + " WHERE key = ?",
            (*values, timestamp, key),
        )
        ArchiveJournal(self.archive).mark_reversed(self.event_id, timestamp)
        self.archive._log_event(
            "undo", "semantic", key, None, f"undo:{operation}#{self.event_id}", "undo"
        )
        self.api.logger.info(
            "Undid memory event %d (%s on %s)", self.event_id, operation, key
        )
        return True, f"undid {operation} on {key}"


class LinkInverse:
    def __init__(self, archive, event, event_id):
        self.archive, self.event, self.event_id = archive, event, event_id
        self.api = contract()

    def apply(self):
        operation = self.event["event_type"]
        payload = self.event.get(
            "new_value" if operation == "link_add" else "old_value"
        )
        try:
            edge = json.loads(payload or "{}")
        except (json.JSONDecodeError, TypeError):
            return False, "link event payload is unreadable"
        if not edge.get("from_ref") or not edge.get("link_type"):
            return False, "link event payload is incomplete"
        timestamp = self.api._now_iso()
        if operation == "link_add":
            self.remove(edge)
        elif operation == "link_remove":
            try:
                self.restore(edge)
            except ValueError as exc:
                return False, f"cannot restore link: {exc}"
        else:
            return False, f"event type {operation!r} is not reversible"
        ArchiveJournal(self.archive).mark_reversed(self.event_id, timestamp)
        self.api.logger.info("Undid link event %d (%s)", self.event_id, operation)
        return True, f"undid {operation} on {edge['from_ref']}"

    def remove(self, edge):
        self.archive.db.execute(
            "DELETE FROM mem_links WHERE from_kind = ? AND from_ref = ? "
            "AND IFNULL(to_entity, '') = ? AND IFNULL(to_ref, '') = ? AND link_type = ?",
            (
                edge.get("from_kind", ""),
                edge["from_ref"],
                edge.get("to_entity") or "",
                edge.get("to_ref") or "",
                edge["link_type"],
            ),
        )
        if edge.get("to_entity"):
            self.archive.db.execute(
                "UPDATE mem_link_stats SET inbound_count = MAX(0, inbound_count - 1) WHERE entity_id = ?",
                (edge["to_entity"],),
            )

    def restore(self, edge):
        self.archive.graph.add_link(
            from_kind=edge.get("from_kind", "semantic"),
            from_ref=edge["from_ref"],
            to_entity=edge.get("to_entity"),
            to_ref=edge.get("to_ref"),
            link_type=edge["link_type"],
            source="undo",
        )
