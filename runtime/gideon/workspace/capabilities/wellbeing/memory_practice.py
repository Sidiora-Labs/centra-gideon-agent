"""Canonical memory-card artifacts and versioned, self-graded practice schedules."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from gideon.workspace.artifacts.native import NativeArtifactProvider

from .store import MeasurementError, MeasurementStore, instant, text


def content_fields(front, back, tags):
    text(front, "front", 10000)
    text(back, "back", 20000)
    if not isinstance(tags, list) or len(tags) > 20:
        raise MeasurementError("tags must contain at most20 strings")
    for tag in tags:
        text(tag, "tag", 80)
    if len(set(tags)) != len(tags):
        raise MeasurementError("tags must be unique")
    return dict(front=front, back=back, tags=tags)


def next_schedule(previous, grade, at):
    if grade not in ("again", "hard", "good", "easy"):
        raise MeasurementError("grade must be again, hard, good or easy")
    interval, repetitions, lapses = (
        previous["interval_days"],
        previous["repetitions"],
        previous["lapses"],
    )
    if grade == "again":
        interval, repetitions, lapses = 0, 0, lapses + 1
        due = at + timedelta(minutes=10)
    else:
        if grade == "hard":
            interval = max(1, interval * 1.2)
        elif grade == "good":
            interval = (
                1 if repetitions == 0 else 3 if repetitions == 1 else interval * 2
            )
        else:
            interval = max(4, interval * 3)
        interval = min(365, round(interval, 3))
        repetitions += 1
        due = at + timedelta(days=interval)
    return dict(
        due_at=due.isoformat(),
        interval_days=interval,
        repetitions=repetitions,
        lapses=lapses,
        last_grade=grade,
        last_practiced_at=at.isoformat(),
        rules_version=1,
    )


class MemoryPracticeStore(MeasurementStore):
    def __init__(self, home):
        super().__init__(home)
        self.artifacts = NativeArtifactProvider(
            root=self.path.parent.parent / "artifacts"
        )
        with self.connection() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS memory_card_revisions(id TEXT NOT NULL,revision INTEGER NOT NULL,due_at TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(id,revision))"
            )

    def _get(self, db, identity):
        row = db.execute(
            "SELECT data FROM memory_card_revisions WHERE id=? ORDER BY revision DESC LIMIT 1",
            (identity,),
        ).fetchone()
        if not row:
            raise MeasurementError("Memory card not found", 404, "not_found")
        return json.loads(row[0])

    def _public(self, row):
        reference = row["artifact"]
        artifact = self.artifacts.get(reference["slug"], version=reference["version"])
        if artifact is None:
            raise MeasurementError("Memory card artifact unavailable", 404, "not_found")
        if hashlib.sha256(artifact.content.encode()).hexdigest() != reference["sha256"]:
            raise MeasurementError(
                "Memory card artifact hash mismatch", 409, "conflict"
            )
        return dict(row, **json.loads(artifact.content))

    def _artifact(self, fields):
        content = json.dumps(fields, sort_keys=True)
        sha = hashlib.sha256(content.encode()).hexdigest()
        slug = "memory-card-" + sha
        artifact = self.artifacts.get(slug, version=1)
        if artifact is None:
            artifact = self.artifacts.create(
                name=fields["front"][:100],
                content=content,
                kind="document",
                source="import",
                slug=slug,
                readonly=True,
            )
        if artifact.content != content:
            raise MeasurementError("Memory card artifact differs", 409, "conflict")
        return dict(slug=slug, version=1, sha256=sha)

    def _write(self, operation, payload, identity=None):
        if not isinstance(payload, dict):
            raise MeasurementError("Card operation must be an object")
        fields = (
            {"request_id", "front", "back", "source", "tags"}
            if operation == "create"
            else (
                {"request_id", "revision", "front", "back", "tags", "archived"}
                if operation == "update"
                else {"request_id", "revision", "grade"}
            )
        )
        if set(payload) - fields:
            raise MeasurementError("Unknown or immutable card fields")
        request_id = text(payload.get("request_id"), "request_id", 128)
        try:
            fingerprint = json.dumps(
                ["memory-card", operation, identity, payload],
                sort_keys=True,
                allow_nan=False,
            )
        except (ValueError, TypeError) as exc:
            raise MeasurementError("Card payload requires finite JSON values") from exc
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT payload,result FROM requests WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise MeasurementError("Request ID already used", 409, "conflict")
                return json.loads(prior[1])
            at = datetime.now(timezone.utc)
            if operation == "create":
                text(payload.get("source"), "source", 256)
                content = content_fields(
                    payload.get("front"), payload.get("back"), payload.get("tags", [])
                )
                row = dict(
                    id=str(uuid4()),
                    revision=1,
                    source=payload["source"],
                    created_at=at.isoformat(),
                    archived=False,
                    artifact=self._artifact(content),
                    schedule=dict(
                        due_at=at.isoformat(),
                        interval_days=0,
                        repetitions=0,
                        lapses=0,
                        last_grade=None,
                        last_practiced_at=None,
                        rules_version=1,
                    ),
                    practice=None,
                )
            else:
                row = self._get(db, identity)
                if (
                    type(payload.get("revision")) is not int
                    or payload["revision"] != row["revision"]
                ):
                    raise MeasurementError(
                        "Memory card changed; reload", 409, "conflict"
                    )
                row["revision"] += 1
                row["practice"] = None
                if operation == "update":
                    previous = self._public(row)
                    content = content_fields(
                        payload.get("front", previous["front"]),
                        payload.get("back", previous["back"]),
                        payload.get("tags", previous["tags"]),
                    )
                    archived = payload.get("archived", row["archived"])
                    if type(archived) is not bool:
                        raise MeasurementError("archived must be boolean")
                    row.update(artifact=self._artifact(content), archived=archived)
                else:
                    if row["archived"]:
                        raise MeasurementError(
                            "Memory card is archived", 409, "conflict"
                        )
                    old_due = row["schedule"]["due_at"]
                    row["schedule"] = next_schedule(
                        row["schedule"], payload.get("grade"), at
                    )
                    row["practice"] = dict(
                        grade=payload["grade"],
                        practiced_at=at.isoformat(),
                        previous_due_at=old_due,
                        next_due_at=row["schedule"]["due_at"],
                    )
            db.execute(
                "INSERT INTO memory_card_revisions VALUES(?,?,?,?)",
                (
                    row["id"],
                    row["revision"],
                    instant(row["schedule"]["due_at"]),
                    json.dumps(row),
                ),
            )
            result = self._public(row)
            db.execute(
                "INSERT INTO requests VALUES(?,?,?)",
                (request_id, fingerprint, json.dumps(result)),
            )
            return result

    def create(self, payload):
        return self._write("create", payload)

    def update(self, identity, payload):
        return self._write("update", payload, identity)

    def practice(self, identity, payload):
        return self._write("practice", payload, identity)

    def get(self, identity):
        with self.connection() as db:
            return self._public(self._get(db, identity))

    def list_cards(self, due_only=False, as_of=None, include_archived=False, limit=100):
        if (
            type(due_only) is not bool
            or type(include_archived) is not bool
            or type(limit) is not int
            or not 1 <= limit <= 500
        ):
            raise MeasurementError("Boolean filters and limit1..500 are required")
        at = (
            instant(as_of)
            if as_of
            else datetime.now(timezone.utc).isoformat(timespec="microseconds")
        )
        with self.connection() as db:
            rows = db.execute(
                """SELECT r.data FROM memory_card_revisions r WHERE revision=(SELECT MAX(s.revision) FROM memory_card_revisions s WHERE s.id=r.id)
                AND (? OR json_extract(data,'$.archived')=0) AND (?=0 OR due_at<=?) ORDER BY due_at,id LIMIT ?""",
                (include_archived, due_only, at, limit),
            )
            return [self._public(json.loads(row[0])) for row in rows]

    def history(self, identity):
        with self.connection() as db:
            self._get(db, identity)
            return [
                self._public(json.loads(row[0]))
                for row in db.execute(
                    "SELECT data FROM memory_card_revisions WHERE id=? ORDER BY revision",
                    (identity,),
                )
            ]
