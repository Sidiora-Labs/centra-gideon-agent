"""Authored musical canons with pinned parts, timing and immutable practice history."""

import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .store import DomainError, integer, text


class RoundStore:
    def __init__(self, root, catalog):
        self.root, self.catalog = Path(root), catalog
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "rounds.sqlite3"
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS rounds (id TEXT PRIMARY KEY,payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS versions (id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(id,revision))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS practice (id TEXT PRIMARY KEY,round_id TEXT,fingerprint TEXT,payload TEXT)"
            )
            db.execute("PRAGMA user_version=1")

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def _read(self, db, item_id):
        row = db.execute("SELECT payload FROM rounds WHERE id=?", (item_id,)).fetchone()
        if row is None:
            raise DomainError("Round not found", 404, "not_found")
        return json.loads(row[0])

    def _write(self, db, item):
        payload = json.dumps(item)
        db.execute("INSERT OR REPLACE INTO rounds VALUES (?,?)", (item["id"], payload))
        db.execute(
            "INSERT INTO versions VALUES (?,?,?)",
            (item["id"], item["revision"], payload),
        )

    def _validate(self, db, item):
        item["title"] = text(item["title"], "title", 200, True)
        item["notes"] = text(item["notes"], "notes", 10000)
        integer(item["tempo_bpm"], "tempo BPM", 20, 300)
        integer(item["meter_beats"], "meter beats", 1, 16)
        if type(item["archived"]) is not bool:
            raise DomainError("Archived must be boolean")
        parts = item["parts"]
        if not isinstance(parts, list) or not 1 <= len(parts) <= 16:
            raise DomainError("A round requires one to sixteen parts")
        ids = set()
        for part in parts:
            if isinstance(part, dict):
                part.setdefault("catalog_ref", None)
            if not isinstance(part, dict) or set(part) != {
                "id",
                "name",
                "entry_beats",
                "notation",
                "catalog_ref",
            }:
                raise DomainError("Invalid part fields")
            part["id"] = text(part["id"], "part ID", 100, True)
            part["name"] = text(part["name"], "part name", 100, True)
            part["notation"] = text(part["notation"], "notation", 20000)
            beat = part["entry_beats"]
            if (
                type(beat) not in (int, float)
                or not math.isfinite(beat)
                or not 0 <= beat <= 10000
            ):
                raise DomainError("Invalid entry beat")
            if part["id"] in ids:
                raise DomainError("Part IDs must be unique")
            ids.add(part["id"])
            ref = part["catalog_ref"]
            if ref is not None:
                if not isinstance(ref, dict) or set(ref) != {"track_id", "render_id"}:
                    raise DomainError("Part audio requires a catalog track and render")
                track = self.catalog.get(
                    "tracks", text(ref["track_id"], "track ID", 100, True)
                )
                render = next(
                    (row for row in track["renders"] if row["id"] == ref["render_id"]),
                    None,
                )
                if not render or render["source"]["kind"] != "imported":
                    raise DomainError(
                        "Part requires an existing imported recording",
                        404,
                        "audio_not_found",
                    )
                artifact = render["artifact_ref"]
                if (
                    self.catalog.artifacts.raw_bytes(
                        artifact["slug"], version=artifact["version"]
                    )
                    is None
                ):
                    raise DomainError(
                        "Part recording bytes are missing", 404, "audio_not_found"
                    )
        partners = item["partner_ids"]
        if (
            not isinstance(partners, list)
            or len(partners) > 16
            or any(not isinstance(value, str) for value in partners)
            or len(set(partners)) != len(partners)
        ):
            raise DomainError("Invalid partner IDs")
        linked = [self._read(db, value) for value in partners if value != item["id"]]
        if item["id"] in partners:
            raise DomainError("A round cannot partner itself")
        inbound = db.execute(
            "SELECT payload FROM rounds WHERE id!=? AND EXISTS(SELECT 1 FROM json_each(payload,'$.partner_ids') WHERE value=?)",
            (item["id"], item["id"]),
        )
        linked += [json.loads(row[0]) for row in inbound]
        if any(
            (row["tempo_bpm"], row["meter_beats"])
            != (item["tempo_bpm"], item["meter_beats"])
            for row in linked
        ):
            raise DomainError(
                "Partner rounds require matching tempo and meter",
                409,
                "incompatible_partner",
            )

    def create(self, data):
        if not isinstance(data, dict) or set(data) - {
            "title",
            "tempo_bpm",
            "meter_beats",
            "notes",
            "parts",
            "partner_ids",
        }:
            raise DomainError("Invalid round fields")
        item = {
            "id": str(uuid4()),
            "title": "",
            "tempo_bpm": 100,
            "meter_beats": 4,
            "notes": "",
            "parts": [],
            "partner_ids": [],
            "revision": 1,
            "archived": False,
            **data,
        }
        with self._db() as db:
            self._validate(db, item)
            self._write(db, item)
        return item

    def get(self, item_id):
        with self._db() as db:
            return self._read(db, item_id)

    def list(self, offset=0, limit=50):
        integer(offset, "offset", 0, 1000000)
        integer(limit, "limit", 1, 100)
        with self._db() as db:
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM rounds ORDER BY id LIMIT ? OFFSET ?",
                    (limit, offset),
                )
            ]

    def update(self, item_id, data):
        if not isinstance(data, dict) or set(data) - {
            "title",
            "tempo_bpm",
            "meter_beats",
            "notes",
            "parts",
            "partner_ids",
            "revision",
            "archived",
        }:
            raise DomainError("Invalid round fields")
        revision = integer(data.get("revision"), "revision", 1, 1000000)
        with self._db() as db:
            item = self._read(db, item_id)
            if item["revision"] != revision:
                raise DomainError(
                    "Round changed; reload before editing", 409, "revision_conflict"
                )
            item.update(data)
            item["revision"] += 1
            self._validate(db, item)
            self._write(db, item)
            return item

    def history(self, item_id):
        with self._db() as db:
            self._read(db, item_id)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM practice WHERE round_id=? ORDER BY rowid",
                    (item_id,),
                )
            ]

    def practice(self, item_id, data):
        if not isinstance(data, dict) or set(data) != {
            "request_id",
            "round_revision",
            "part_ids",
            "occurred_at",
            "grade",
            "notes",
        }:
            raise DomainError("Invalid practice fields")
        request_id = text(data["request_id"], "request ID", 100, True)
        integer(data["round_revision"], "round revision", 1, 1000000)
        integer(data["grade"], "grade", 0, 5)
        text(data["notes"], "practice notes", 5000)
        try:
            date = datetime.fromisoformat(
                text(data["occurred_at"], "practice date", 64, True)
            )
            if date.utcoffset() is None:
                raise ValueError("offset required")
        except ValueError as exc:
            raise DomainError(
                "Practice date requires an ISO timestamp with offset"
            ) from exc
        fingerprint = json.dumps({"round_id": item_id, **data}, sort_keys=True)
        with self._db() as db:
            prior = db.execute(
                "SELECT fingerprint,payload FROM practice WHERE id=?", (request_id,)
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError(
                        "Practice request ID already used", 409, "request_conflict"
                    )
                return {"item": json.loads(prior[1]), "replayed": True}
            item = self._read(db, item_id)
            if item["revision"] != data["round_revision"]:
                raise DomainError(
                    "Round changed before practice", 409, "revision_conflict"
                )
            parts = data["part_ids"]
            if (
                not isinstance(parts, list)
                or not parts
                or any(not isinstance(value, str) for value in parts)
                or len(set(parts)) != len(parts)
                or not set(parts) <= {part["id"] for part in item["parts"]}
            ):
                raise DomainError("Practice must select existing unique parts")
            practice = {
                "id": request_id,
                "round_id": item_id,
                **data,
                "occurred_at": date.astimezone(timezone.utc).isoformat(),
                "part_snapshot": [
                    part for part in item["parts"] if part["id"] in parts
                ],
            }
            db.execute(
                "INSERT INTO practice VALUES (?,?,?,?)",
                (request_id, item_id, fingerprint, json.dumps(practice)),
            )
            return {"item": practice, "replayed": False}
