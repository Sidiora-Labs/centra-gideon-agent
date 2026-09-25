"""Human goals and planned sessions; separate from autonomous execution goals."""

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.workspace.capabilities.identity.store import ConflictError


def _text(value, name, maximum, empty=False):
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not empty and not value.strip())
    ):
        raise ValueError(f"Invalid {name}")


def _instant(value):
    if not isinstance(value, str):
        raise ValueError("Session times require an explicit timezone offset")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError("Session times require an explicit timezone offset")
    return parsed.astimezone(timezone.utc).isoformat(timespec="microseconds")


class GoalStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path) as db:
            db.executescript(
                "CREATE TABLE IF NOT EXISTS goals(id TEXT PRIMARY KEY, body TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, body TEXT NOT NULL);"
                "CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, body TEXT NOT NULL);"
            )

    @staticmethod
    def _get(db, table, id):
        row = db.execute(f"SELECT body FROM {table} WHERE id=?", (id,)).fetchone()
        if row is None:
            raise KeyError("Human planning record not found")
        return json.loads(row[0])

    def _list(self, table):
        with sqlite3.connect(self.path) as db:
            rows = [
                json.loads(row[0])
                for row in db.execute(f"SELECT body FROM {table} ORDER BY id")
            ]
        return sorted(
            rows, key=lambda row: (row.get("start_at", row["created_at"]), row["id"])
        )

    def list_goals(self):
        return self._list("goals")

    def list_sessions(self):
        return self._list("sessions")

    def get_goal(self, id):
        with sqlite3.connect(self.path) as db:
            return self._get(db, "goals", id)

    def get_session(self, id):
        with sqlite3.connect(self.path) as db:
            return self._get(db, "sessions", id)

    def _save(self, table, fields, id, expected_revision, request_id):
        _text(request_id, "request_id", 128)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("expected_revision must be a nonnegative integer")
        if id is not None:
            _text(id, "id", 128)
        fingerprint = json.dumps([table, fields, id, expected_revision], sort_keys=True)
        with sqlite3.connect(self.path) as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT fingerprint,body FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise ConflictError(
                        "request_id already names a different planning change"
                    )
                return json.loads(prior[1])
            old = self._get(db, table, id) if id else None
            if (old["revision"] if old else 0) != expected_revision:
                raise ConflictError("Planning record changed; reload before saving")
            sessions = [
                json.loads(row[0]) for row in db.execute("SELECT body FROM sessions")
            ]
            if (
                table == "goals"
                and fields["status"] != "active"
                and any(
                    row["goal_id"] == id and row["status"] == "scheduled"
                    for row in sessions
                )
            ):
                raise ConflictError(
                    "Resolve scheduled sessions before closing this goal"
                )
            if table == "sessions":
                goal = self._get(db, "goals", fields["goal_id"])
                if fields["status"] == "scheduled":
                    if goal["status"] != "active":
                        raise ConflictError(
                            "Only active goals can have scheduled sessions"
                        )
                    if any(
                        row["id"] != id
                        and row["status"] == "scheduled"
                        and row["start_at"] < fields["end_at"]
                        and fields["start_at"] < row["end_at"]
                        for row in sessions
                    ):
                        raise ConflictError(
                            "Scheduled session overlaps an existing session"
                        )
            now = datetime.now(timezone.utc).isoformat()
            result = {
                **fields,
                "id": id or uuid4().hex,
                "revision": expected_revision + 1,
                "created_at": old["created_at"] if old else now,
                "updated_at": now,
            }
            db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?)",
                (result["id"], json.dumps(result)),
            )
            db.execute(
                "INSERT INTO requests VALUES (?,?,?)",
                (request_id, fingerprint, json.dumps(result)),
            )
            return result

    def save_goal(
        self,
        *,
        title,
        request_id,
        description="",
        status="active",
        target_date=None,
        id=None,
        expected_revision=0,
    ):
        _text(title, "title", 200)
        _text(description, "description", 10000, True)
        if status not in ("active", "completed", "archived"):
            raise ValueError("Invalid goal status")
        if target_date is not None:
            if (
                not isinstance(target_date, str)
                or date.fromisoformat(target_date).isoformat() != target_date
            ):
                raise ValueError("target_date must be YYYY-MM-DD or null")
        return self._save(
            "goals",
            dict(
                title=title,
                description=description,
                status=status,
                target_date=target_date,
            ),
            id,
            expected_revision,
            request_id,
        )

    def save_session(
        self,
        *,
        goal_id,
        title,
        start_at,
        end_at,
        request_id,
        status="scheduled",
        notes="",
        id=None,
        expected_revision=0,
    ):
        _text(goal_id, "goal_id", 128)
        _text(title, "title", 200)
        _text(notes, "notes", 10000, True)
        if status not in ("scheduled", "completed", "cancelled"):
            raise ValueError("Invalid session status")
        start_at, end_at = _instant(start_at), _instant(end_at)
        duration = datetime.fromisoformat(end_at) - datetime.fromisoformat(start_at)
        if not 0 < duration.total_seconds() <= 86400:
            raise ValueError("Session duration must be positive and at most 24 hours")
        return self._save(
            "sessions",
            dict(
                goal_id=goal_id,
                title=title,
                start_at=start_at,
                end_at=end_at,
                status=status,
                notes=notes,
            ),
            id,
            expected_revision,
            request_id,
        )

    def calendar(self):
        def escape(value):
            return (
                value.replace("\\", "\\\\")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .replace("\n", "\\n")
                .replace(";", "\\;")
                .replace(",", "\\,")
            )

        def stamp(value):
            return datetime.fromisoformat(value).strftime("%Y%m%dT%H%M%SZ")

        lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//Gideon//Human Planning//EN",
            "CALSCALE:GREGORIAN",
        ]
        for row in self.list_sessions():
            lines += [
                "BEGIN:VEVENT",
                f"UID:{row['id']}@gideon.local",
                f"DTSTAMP:{stamp(row['updated_at'])}",
                f"DTSTART:{stamp(row['start_at'])}",
                f"DTEND:{stamp(row['end_at'])}",
                f"SEQUENCE:{row['revision']}",
                "SUMMARY:" + escape(row["title"]),
                "DESCRIPTION:" + escape(row["notes"]),
                "STATUS:"
                + ("CANCELLED" if row["status"] == "cancelled" else "CONFIRMED"),
                "RELATED-TO:" + row["goal_id"] + "@gideon.local",
                "END:VEVENT",
            ]
        lines.append("END:VCALENDAR")
        folded = []
        for line in lines:
            part = ""
            for char in line:
                if len((part + char).encode()) > 75:
                    folded.append(part)
                    part = " "
                part += char
            folded.append(part)
        return "\r\n".join(folded) + "\r\n"
