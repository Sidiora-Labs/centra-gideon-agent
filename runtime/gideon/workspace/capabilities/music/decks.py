"""Persisted card decks, canonical artwork and real media-job linkage."""

import io
import json
import math
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from PIL import Image

from .deck_layout import compose, pdf_bytes, roster
from .listening import digest
from .store import DomainError, integer, text

EDITABLE = {
    "name",
    "description",
    "style_notes",
    "layout_prompt",
    "negative_prompt",
    "orientation",
    "width_mm",
    "height_mm",
    "bleed_mm",
    "safe_mm",
    "sample_refs",
    "context_ref",
    "archived",
}


class DeckStore:
    def __init__(self, home, artifacts, jobs=None):
        self.home, self.artifacts = Path(home), artifacts
        self.root = self.home / "capabilities" / "music"
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "decks.sqlite3"
        if jobs is None:
            from gideon.workspace.capabilities.media.jobs import MediaJobs
            from gideon.workspace.capabilities.media.sketches import SketchStore

            jobs = MediaJobs(
                self.home / "capabilities/media/jobs.sqlite3",
                SketchStore(
                    self.home / "capabilities/media/sketches.sqlite3", artifacts
                ),
            )
        self.jobs = jobs
        with self._db() as db:
            db.execute("PRAGMA user_version=1")
            db.execute(
                "CREATE TABLE IF NOT EXISTS decks(id TEXT PRIMARY KEY,payload TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS history(id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(id,revision))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS exports(id TEXT,revision INTEGER,payload TEXT,PRIMARY KEY(id,revision))"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS renders(request_id TEXT PRIMARY KEY,fingerprint TEXT,payload TEXT)"
            )

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

    def _get(self, db, item_id):
        row = db.execute("SELECT payload FROM decks WHERE id=?", (item_id,)).fetchone()
        if not row:
            raise DomainError("Deck not found", 404, "not_found")
        item = json.loads(row[0])
        item["exports"] = [
            json.loads(row[0])
            for row in db.execute(
                "SELECT payload FROM exports WHERE id=? ORDER BY revision", (item_id,)
            )
        ]
        return item

    def get(self, item_id):
        with self._db() as db:
            return self._get(db, item_id)

    def list(self):
        with self._db() as db:
            return [
                self._get(db, row[0])
                for row in db.execute(
                    "SELECT id FROM decks ORDER BY rowid DESC LIMIT 100"
                )
            ]

    def history(self, item_id):
        with self._db() as db:
            self._get(db, item_id)
            return [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT payload FROM history WHERE id=? ORDER BY revision DESC",
                    (item_id,),
                )
            ]

    def _save(self, db, item):
        item["completion"] = {
            "total": len(item["cards"]),
            "artwork": sum(card["artifact_ref"] is not None for card in item["cards"]),
            "prompted": sum(bool(card["prompt"]) for card in item["cards"]),
        }
        encoded = json.dumps(item)
        db.execute("INSERT OR REPLACE INTO decks VALUES (?,?)", (item["id"], encoded))
        db.execute(
            "INSERT INTO history VALUES (?,?,?)",
            (item["id"], item["revision"], encoded),
        )
        return item

    def create(self, data):
        if (
            not isinstance(data, dict)
            or set(data) != {"name", "kind"}
            or data["kind"] not in ("playing", "tarot")
        ):
            raise DomainError("Deck name and playing/tarot kind required")
        name = text(data["name"], "name", 120, True)
        tarot = data["kind"] == "tarot"
        item = {
            "id": str(uuid4()),
            "revision": 1,
            "name": name,
            "kind": data["kind"],
            "description": "",
            "style_notes": "",
            "layout_prompt": "Framed portrait card with legible indices",
            "negative_prompt": "",
            "orientation": "one_way" if tarot else "two_way",
            "width_mm": 69.85 if tarot else 63.5,
            "height_mm": 120.65 if tarot else 88.9,
            "bleed_mm": 3,
            "safe_mm": 4,
            "sample_refs": [],
            "context_ref": None,
            "archived": False,
            "cards": roster(data["kind"]),
            "exports": [],
        }
        with self._db() as db:
            return self._save(db, item)

    def _guard(self, item, data):
        integer(data.get("revision"), "revision", 1, 1000000)
        if data["revision"] != item["revision"]:
            raise DomainError("Deck revision changed", 409, "revision_conflict")

    def _ref(self, ref, kind):
        if not isinstance(ref, dict) or set(ref) != {"slug", "version"}:
            raise DomainError("Pinned artifact reference required")
        text(ref["slug"], "slug", 200, True)
        integer(ref["version"], "version", 1, 1000000)
        artifact = self.artifacts.get(ref["slug"], version=ref["version"])
        if not artifact or artifact.kind != kind:
            raise DomainError(
                "Referenced artifact unavailable", 404, "artifact_not_found"
            )
        return artifact

    def image(self, ref):
        self._ref(ref, "image")
        raw = self.artifacts.raw_bytes(ref["slug"], version=ref["version"])
        if not raw or len(raw[0]) > 16 * 1024 * 1024:
            raise DomainError("Card image unavailable or too large")
        try:
            image = Image.open(io.BytesIO(raw[0]))
            if (
                image.width > 4096
                or image.height > 4096
                or getattr(image, "n_frames", 1) != 1
            ):
                raise DomainError("Card image dimensions exceed supported bounds")
            image.load()
            return image.convert("RGBA")
        except (OSError, ValueError, SyntaxError) as exc:
            raise DomainError("Card image cannot be decoded") from exc

    def update(self, item_id, data):
        if (
            not isinstance(data, dict)
            or "revision" not in data
            or set(data) - EDITABLE - {"revision"}
        ):
            raise DomainError("Invalid deck update fields")
        with self._db() as db:
            item = self._get(db, item_id)
            self._guard(item, data)
            item.update(
                {key: value for key, value in data.items() if key != "revision"}
            )
            for key, limit in (
                ("name", 120),
                ("description", 2000),
                ("style_notes", 2000),
                ("layout_prompt", 1000),
                ("negative_prompt", 1000),
            ):
                text(item[key], key, limit, key == "name")
            if (
                item["orientation"] not in ("two_way", "one_way")
                or type(item["archived"]) is not bool
            ):
                raise DomainError("Invalid orientation or archive state")
            for key, low, high in (
                ("width_mm", 30, 150),
                ("height_mm", 40, 200),
                ("bleed_mm", 0, 10),
                ("safe_mm", 2, 15),
            ):
                value = item[key]
                if (
                    type(value) not in (int, float)
                    or not math.isfinite(value)
                    or not low <= value <= high
                ):
                    raise DomainError("Invalid physical card dimensions")
            if (
                item["width_mm"] <= item["safe_mm"] * 2 + 10
                or item["height_mm"] <= item["safe_mm"] * 2 + 15
            ):
                raise DomainError("Safe area consumes card")
            if (
                not isinstance(item["sample_refs"], list)
                or len(item["sample_refs"]) > 12
            ):
                raise DomainError("At most twelve style references")
            for ref in item["sample_refs"]:
                self.image(ref)
            if item["context_ref"] is not None:
                self._ref(item["context_ref"], "json")
            item["revision"] += 1
            return self._save(db, item)

    def _card(self, item, key):
        card = next((card for card in item["cards"] if card["key"] == key), None)
        if card is None:
            raise DomainError("Card not found", 404, "not_found")
        return card

    def card(self, item_id, key, data):
        if not isinstance(data, dict) or set(data) != {
            "revision",
            "name",
            "prompt",
            "negative_prompt",
            "artifact_ref",
        }:
            raise DomainError("Invalid card fields")
        with self._db() as db:
            item = self._get(db, item_id)
            self._guard(item, data)
            card = self._card(item, key)
            text(data["name"], "name", 120, True)
            text(data["prompt"], "prompt", 2000)
            text(data["negative_prompt"], "negative prompt", 1000)
            if data["artifact_ref"] is not None:
                self.image(data["artifact_ref"])
            card.update(
                {key: value for key, value in data.items() if key != "revision"}
            )
            item["revision"] += 1
            return self._save(db, item)

    def export(self, item_id, data):
        if not isinstance(data, dict) or set(data) != {"revision"}:
            raise DomainError("Export revision required")
        with self._db() as db:
            item = self._get(db, item_id)
            self._guard(item, data)
            prior = next(
                (row for row in item["exports"] if row["revision"] == item["revision"]),
                None,
            )
            if prior:
                return prior
            images = {
                card["key"]: self.image(card["artifact_ref"])
                for card in item["cards"]
                if card["artifact_ref"]
            }
            try:
                for card in item["cards"]:
                    card["name"].encode("latin-1")
            except UnicodeEncodeError as exc:
                raise DomainError(
                    "PDF lettering currently supports Latin-1; rename unsupported card lettering before export",
                    422,
                    "unsupported_pdf_glyph",
                ) from exc
            document = pdf_bytes(item, images)
            pdf = self.artifacts.create_binary(
                name=item["name"] + " print cards",
                data=document,
                mime="application/pdf",
                kind="pdf",
                source="chat",
            )
            manifest = self.artifacts.create(
                name=item["name"] + " deck manifest",
                content=json.dumps(item, indent=2),
                kind="json",
                source="chat",
            )
            receipt = {
                "revision": item["revision"],
                "pdf_ref": {"slug": pdf.slug, "version": pdf.version},
                "manifest_ref": {"slug": manifest.slug, "version": manifest.version},
            }
            db.execute(
                "INSERT INTO exports VALUES (?,?,?)",
                (item_id, item["revision"], json.dumps(receipt)),
            )
            return receipt

    def generate(self, item_id, data):
        if not isinstance(data, dict) or set(data) != {
            "revision",
            "request_id",
            "card_keys",
            "size",
            "controls",
        }:
            raise DomainError("Invalid deck rendering fields")
        text(data["request_id"], "request ID", 100, True)
        keys = data["card_keys"]
        if (
            not isinstance(keys, list)
            or not 1 <= len(keys) <= 16
            or any(not isinstance(key, str) for key in keys)
            or len(set(keys)) != len(keys)
        ):
            raise DomainError("Select one to sixteen unique cards")
        fingerprint = digest([item_id, data])
        with self._db() as db:
            prior = db.execute(
                "SELECT fingerprint,payload FROM renders WHERE request_id=?",
                (data["request_id"],),
            ).fetchone()
            if prior:
                if prior[0] != fingerprint:
                    raise DomainError(
                        "Render request ID conflict", 409, "request_conflict"
                    )
                return json.loads(prior[1])
            item = self._get(db, item_id)
            self._guard(item, data)
            cards = [self._card(item, key) for key in keys]
            inputs = [
                {
                    "prompt": compose(item, card)["prompt"],
                    "size": data["size"],
                    "controls": data["controls"],
                }
                for card in cards
            ]
            for body in inputs:
                self.jobs.images.prepare(body)
            rows = []
            for card, body in zip(cards, inputs):
                job = self.jobs.submit(
                    {
                        "operation": "image_generate",
                        "request_id": "deck-"
                        + digest([item_id, data["request_id"], card["key"]]),
                        "input": body,
                    }
                )
                rows.append(
                    {
                        "deck_id": item_id,
                        "card_key": card["key"],
                        "job_id": job["id"],
                        "revision": item["revision"],
                        "status": job["status"],
                    }
                )
            receipt = {"jobs": rows}
            db.execute(
                "INSERT INTO renders VALUES (?,?,?)",
                (data["request_id"], fingerprint, json.dumps(receipt)),
            )
            return receipt

    def adopt(self, item_id, key, data):
        if not isinstance(data, dict) or set(data) != {"revision", "job_id"}:
            raise DomainError("Adoption requires revision and job_id")
        with self._db() as db:
            item = self._get(db, item_id)
            self._guard(item, data)
            card = self._card(item, key)
            linked = any(
                row["deck_id"] == item_id
                and row["card_key"] == key
                and row["job_id"] == data["job_id"]
                for result in db.execute("SELECT payload FROM renders")
                for row in json.loads(result[0])["jobs"]
            )
            if not linked:
                raise DomainError(
                    "Job does not belong to this card", 409, "job_conflict"
                )
            job = self.jobs.get(data["job_id"])
            if job["status"] != "succeeded" or not job.get("result"):
                raise DomainError(
                    "Card render has not succeeded", 409, "job_incomplete"
                )
            result = job["result"]
            ref = {"slug": result["artifact_id"], "version": result["version"]}
            self.image(ref)
            card["artifact_ref"] = ref
            item["revision"] += 1
            return self._save(db, item)
