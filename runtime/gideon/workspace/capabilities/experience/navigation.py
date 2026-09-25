"""Durable receipts distinguish requested navigation from browser acknowledgement."""

import json
import re
from datetime import datetime, timezone
from uuid import uuid4

from .graph import identifier, revision, text
from .narration import digest
from .store import Conflict, NotFound


def route(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9_/-]{0,199}", value) or "//" in value or value.endswith("/"):
        raise ValueError("route must be a local console destination")
    return value


class NavigationReceipts:
    def __init__(self, store):
        self.store = store
        with store.connection() as db:
            db.execute("CREATE TABLE IF NOT EXISTS navigation_receipts(id TEXT PRIMARY KEY, request_id TEXT UNIQUE, input TEXT, body TEXT)")

    def get(self, key):
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM navigation_receipts WHERE id=?", (identifier(key),)).fetchone()
            if row is None:
                raise NotFound("navigation receipt not found")
            return json.loads(row[0])

    def list(self):
        with self.store.connection() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT body FROM navigation_receipts ORDER BY rowid DESC LIMIT 100")]

    def request(self, body):
        if not isinstance(body, dict) or set(body) != {"request_id", "command", "origin", "target", "input_origin"}:
            raise ValueError("navigation requires request_id, command, origin, target and input_origin")
        request_id = identifier(body["request_id"])
        command, origin, target = text(body["command"], 400), route(body["origin"]), route(body["target"])
        if body["input_origin"] not in ("typed", "voice"):
            raise ValueError("input_origin must be typed or voice")
        with self.store.connection() as db:
            previous = db.execute("SELECT input,body FROM navigation_receipts WHERE request_id=?", (request_id,)).fetchone()
            if previous:
                if previous[0] != digest(body):
                    raise Conflict("navigation request identifier was used with different input")
                return json.loads(previous[1])
            receipt = {"id": uuid4().hex, "request_id": request_id, "command": command, "origin": origin, "target": target,
                       "input_origin": body["input_origin"], "status": "requested", "observed_route": None, "revision": 1,
                       "created_at": datetime.now(timezone.utc).isoformat(), "applied_at": None}
            db.execute("INSERT INTO navigation_receipts VALUES(?,?,?,?)", (receipt["id"], request_id, digest(body), json.dumps(receipt)))
            return receipt

    def acknowledge(self, key, body):
        if not isinstance(body, dict) or set(body) != {"revision", "observed_route"}:
            raise ValueError("acknowledgement requires revision and observed_route")
        expected, observed = revision(body["revision"]), route(body["observed_route"])
        with self.store.connection() as db:
            row = db.execute("SELECT body FROM navigation_receipts WHERE id=?", (identifier(key),)).fetchone()
            if row is None:
                raise NotFound("navigation receipt not found")
            receipt = json.loads(row[0])
            if expected != 1 or observed != receipt["target"]:
                raise Conflict("the browser has not acknowledged the requested destination")
            if receipt["status"] == "applied":
                return receipt
            receipt.update(status="applied", observed_route=observed, revision=2, applied_at=datetime.now(timezone.utc).isoformat())
            db.execute("UPDATE navigation_receipts SET body=? WHERE id=?", (json.dumps(receipt), key))
            return receipt
