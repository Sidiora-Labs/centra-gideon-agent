import hashlib
import json
import math
from uuid import uuid4

from .store import Conflict, NotFound
from .worlds import ASSET, get_worlds, identifier

SCHEMA_VERSION = 1
CONTROLLER_KIND = "ambient_beacon"


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _fingerprint(candidate):
    return hashlib.sha256(_canonical(candidate).encode()).hexdigest()


def _text(value, label, limit=80):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value.strip()) > limit
        or any(ord(char) < 32 for char in value)
    ):
        raise ValueError(f"{label} must contain 1 to {limit} visible characters")
    return value.strip()


def _position(value):
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(
            type(item) not in (int, float)
            or not math.isfinite(item)
            or abs(item) > 10000
            for item in value
        )
    ):
        raise ValueError("Controller position requires three bounded coordinates")
    return value


class WorldFoundations:
    def __init__(self, store, worlds=None):
        self.store, self.worlds = store, worlds or get_worlds(store)
        self.instance_id = hashlib.sha256(
            str(store.path.parent.parent.resolve()).encode()
        ).hexdigest()[:24]
        with store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS world_foundations(id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS world_controllers(id TEXT PRIMARY KEY, body TEXT NOT NULL);
            """)

    def _get(self, table, key):
        identifier(key, 96)
        with self.store.connection() as db:
            row = db.execute(f"SELECT body FROM {table} WHERE id=?", (key,)).fetchone()
        if not row:
            raise NotFound(
                "foundation not found"
                if table == "world_foundations"
                else "controller not found"
            )
        return json.loads(row[0])

    def _put(self, table, record):
        with self.store.connection() as db:
            db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES(?,?)",
                (record["id"], _canonical(record)),
            )
        return record

    def list(self):
        with self.store.connection() as db:
            foundations = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM world_foundations ORDER BY rowid DESC LIMIT 200"
                )
            ]
            controllers = [
                json.loads(row[0])
                for row in db.execute(
                    "SELECT body FROM world_controllers ORDER BY rowid DESC LIMIT 200"
                )
            ]
        return {
            "schema_version": SCHEMA_VERSION,
            "instance_id": self.instance_id,
            "foundations": foundations,
            "controllers": controllers,
        }

    def record(self, body):
        if not isinstance(body, dict) or set(body) != {"title", "controller", "style"}:
            raise ValueError("Foundation requires title, controller and style")
        controller = body["controller"]
        if (
            not isinstance(controller, dict)
            or set(controller) != {"kind", "position"}
            or controller["kind"] != CONTROLLER_KIND
        ):
            raise ValueError("Only the built-in ambient_beacon controller is supported")
        style = body["style"]
        if not isinstance(style, dict) or len(_canonical(style)) > 4096:
            raise ValueError("Style must be a bounded local object")
        record = {
            "id": uuid4().hex,
            "revision": 1,
            "state": "draft",
            "title": _text(body["title"], "Foundation title"),
            "controller": {
                "kind": CONTROLLER_KIND,
                "position": _position(controller["position"]),
            },
            "style": style,
            "provenance": {"kind": "authored", "instance_id": self.instance_id},
            "candidate": None,
            "fingerprint": None,
        }
        return self._put("world_foundations", record)

    def package(self, key, revision):
        record = self._expected(key, revision)
        if record["state"] != "draft":
            raise Conflict("Only a draft foundation can be packaged")
        candidate = {
            "schema_version": SCHEMA_VERSION,
            "origin_instance": self.instance_id,
            "foundation_id": record["id"],
            "title": record["title"],
            "controller": record["controller"],
        }
        record.update(
            state="packaged",
            revision=record["revision"] + 1,
            candidate=candidate,
            fingerprint=_fingerprint(candidate),
        )
        return self._put("world_foundations", record)

    def promote(self, key, revision):
        record = self._expected(key, revision)
        if (
            record["state"] != "packaged"
            or _fingerprint(record["candidate"]) != record["fingerprint"]
        ):
            raise Conflict("Only an intact packaged candidate can be promoted")
        record.update(state="promoted", revision=record["revision"] + 1)
        return self._put("world_foundations", record)

    def envelope(self, key):
        record = self._get("world_foundations", key)
        if record["state"] != "promoted":
            raise Conflict("Only a promoted foundation can federate")
        return {
            "schema_version": SCHEMA_VERSION,
            "candidate": record["candidate"],
            "fingerprint": record["fingerprint"],
        }

    def inherit(self, envelope):
        if (
            not isinstance(envelope, dict)
            or set(envelope) != {"schema_version", "candidate", "fingerprint"}
            or envelope["schema_version"] != SCHEMA_VERSION
        ):
            raise ValueError("Foundation envelope is incompatible")
        candidate, fingerprint = envelope["candidate"], envelope["fingerprint"]
        if (
            not isinstance(candidate, dict)
            or set(candidate)
            != {
                "schema_version",
                "origin_instance",
                "foundation_id",
                "title",
                "controller",
            }
            or _fingerprint(candidate) != fingerprint
        ):
            raise Conflict("Foundation candidate fingerprint is invalid")
        if candidate["origin_instance"] == self.instance_id:
            raise Conflict("Cannot inherit a foundation from this instance")
        _text(candidate["title"], "Foundation title")
        controller = candidate["controller"]
        if (
            not isinstance(controller, dict)
            or set(controller) != {"kind", "position"}
            or controller["kind"] != CONTROLLER_KIND
        ):
            raise ValueError("Inherited controller is unsupported")
        _position(controller["position"])
        record = {
            "id": uuid4().hex,
            "revision": 1,
            "state": "inherited",
            "title": candidate["title"],
            "controller": controller,
            "style": None,
            "candidate": candidate,
            "fingerprint": fingerprint,
            "provenance": {
                "kind": "inherited",
                "origin_instance": candidate["origin_instance"],
                "foundation_id": candidate["foundation_id"],
            },
        }
        return self._put("world_foundations", record)

    def adopt(self, key, revision):
        record = self._expected(key, revision)
        if record["state"] != "inherited":
            raise Conflict("Only an inherited foundation can be adopted")
        if _fingerprint(record["candidate"]) != record["fingerprint"]:
            raise Conflict("Inherited foundation fingerprint is invalid")
        record.update(state="adopted", revision=record["revision"] + 1)
        return self._put("world_foundations", record)

    def withdraw(self, key, revision):
        record = self._expected(key, revision)
        if record["state"] != "promoted":
            raise Conflict("Only a promoted foundation can be withdrawn")
        record.update(state="withdrawn", revision=record["revision"] + 1)
        self._put("world_foundations", record)
        return {
            "schema_version": SCHEMA_VERSION,
            "origin_instance": self.instance_id,
            "foundation_id": record["id"],
            "fingerprint": record["fingerprint"],
        }

    def apply_withdrawal(self, withdrawal):
        if not isinstance(withdrawal, dict) or set(withdrawal) != {
            "schema_version",
            "origin_instance",
            "foundation_id",
            "fingerprint",
        }:
            raise ValueError("Withdrawal is invalid")
        for record in self.list()["foundations"]:
            provenance = record["provenance"]
            if (
                provenance.get("origin_instance") == withdrawal["origin_instance"]
                and provenance.get("foundation_id") == withdrawal["foundation_id"]
            ):
                if record["fingerprint"] != withdrawal["fingerprint"]:
                    raise Conflict(
                        "Withdrawal fingerprint does not match inherited foundation"
                    )
                record.update(state="withdrawn", revision=record["revision"] + 1)
                return self._put("world_foundations", record)
        raise NotFound("inherited foundation not found")

    def _expected(self, key, revision):
        if type(revision) is not int or revision < 1:
            raise ValueError("A positive expected revision is required")
        record = self._get("world_foundations", key)
        if record["revision"] != revision:
            raise Conflict("foundation revision changed")
        return record

    async def install_controller(self, body):
        if not isinstance(body, dict) or set(body) != {"foundation_id", "world"}:
            raise ValueError("Controller install requires foundation_id and world")
        foundation = self._get("world_foundations", body["foundation_id"])
        if foundation["state"] not in ("promoted", "adopted"):
            raise Conflict("Controller requires a promoted or adopted foundation")
        world = identifier(body["world"])
        await self.worlds.open(world, {})
        record = {
            "id": uuid4().hex,
            "revision": 1,
            "foundation_id": foundation["id"],
            "world": world,
            "kind": CONTROLLER_KIND,
            "desired_state": "stopped",
            "state": "installed",
            "entity_id": None,
            "last_receipt": None,
        }
        return self._put("world_controllers", record)

    async def control(self, key, operation, revision):
        record = self._get("world_controllers", key)
        if type(revision) is not int or record["revision"] != revision:
            raise Conflict("controller revision changed")
        if operation in ("arm", "restart"):
            record["desired_state"] = "armed"
            receipt = await self._apply(record)
            record.update(
                state="armed",
                entity_id="controller_" + record["id"][:24],
                last_receipt=receipt,
            )
        elif operation == "stop":
            receipt = await self._remove(record)
            record.update(
                desired_state="stopped",
                state="stopped",
                entity_id=None,
                last_receipt=receipt,
            )
        elif operation == "retire":
            receipt = await self._remove(record)
            record.update(
                desired_state="retired",
                state="retired",
                entity_id=None,
                last_receipt=receipt,
            )
        else:
            raise ValueError("Unknown controller lifecycle operation")
        record["revision"] += 1
        return self._put("world_controllers", record)

    async def reconcile(self):
        reconciled = []
        for record in self.list()["controllers"]:
            if record["desired_state"] == "armed":
                record.update(
                    state="armed",
                    entity_id="controller_" + record["id"][:24],
                    last_receipt=await self._apply(record),
                    revision=record["revision"] + 1,
                )
                self._put("world_controllers", record)
                reconciled.append(record)
        return {"reconciled": reconciled}

    async def _apply(self, record):
        foundation = self._get("world_foundations", record["foundation_id"])
        if foundation["state"] == "withdrawn":
            raise Conflict("Withdrawn foundation cannot operate a controller")
        snapshot = await self.worlds.open(record["world"], {})
        entity = "controller_" + record["id"][:24]
        operations = []
        if entity not in snapshot["state"]["entities"]:
            operations.append(
                {
                    "verb": "spawn",
                    "args": {
                        "id": entity,
                        "lib": ASSET,
                        "pos": foundation["controller"]["position"],
                        "yaw": 0,
                    },
                }
            )
        operations.append(
            {
                "verb": "comp",
                "args": {
                    "id": entity,
                    "type": "gideon_controller",
                    "data": {
                        "controller_id": record["id"],
                        "kind": CONTROLLER_KIND,
                        "foundation_id": foundation["id"],
                    },
                },
            }
        )
        return await self.worlds.call(
            record["world"],
            {
                "expected_seq": snapshot["seq"],
                "request_id": "controller_" + uuid4().hex,
                "operations": operations,
                "intent": {
                    "operation": "controller_tick",
                    "controller_id": record["id"],
                },
            },
        )

    async def _remove(self, record):
        snapshot = await self.worlds.open(record["world"], {})
        entity = "controller_" + record["id"][:24]
        operations = (
            []
            if entity not in snapshot["state"]["entities"]
            else [{"verb": "remove", "args": {"id": entity}}]
        )
        if not operations:
            return {"complete": True, "seq": snapshot["seq"], "operations": []}
        return await self.worlds.call(
            record["world"],
            {
                "expected_seq": snapshot["seq"],
                "request_id": "controller_" + uuid4().hex,
                "operations": operations,
                "intent": {
                    "operation": "controller_stop",
                    "controller_id": record["id"],
                },
            },
        )
