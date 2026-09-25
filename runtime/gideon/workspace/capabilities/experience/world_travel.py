import hashlib
import json
import re
import secrets
import time

from .store import Conflict, NotFound


SCOPE = "experience.world_guest"
VERSION = 1


def _identifier(value, limit=96):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1," + str(limit) + r"}", value):
        raise ValueError("Invalid travel identifier")
    return value


def _name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 64 or any(ord(char) < 32 for char in value):
        raise ValueError("Guest name must contain 1 to 64 visible characters")
    return value.strip()


class WorldTravel:
    def __init__(self, store, peers, worlds, now=None):
        self.store, self.peers, self.worlds = store, peers, worlds
        self.now = now or (lambda: int(time.time()))
        with self.store.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS world_guest_visits(
                    id TEXT PRIMARY KEY, ticket_hash TEXT UNIQUE NOT NULL, peer_id TEXT NOT NULL,
                    world TEXT NOT NULL, guest_name TEXT NOT NULL, state TEXT NOT NULL,
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revision INTEGER NOT NULL,
                    CHECK(state IN ('invited','active','left','expired')));
                CREATE TABLE IF NOT EXISTS world_guest_requests(
                    scope TEXT NOT NULL, request_id TEXT NOT NULL, input TEXT NOT NULL,
                    result TEXT NOT NULL, PRIMARY KEY(scope,request_id));
            """)

    def _peer(self, peer_id, direction):
        _identifier(peer_id)
        projection = self.peers.snapshot()
        if projection.get("version") != 1:
            raise Conflict("Canonical peer projection is incompatible")
        peer = next((row for row in projection.get("peers", []) if row.get("id") == peer_id), None)
        if not peer or peer.get("enabled") is not True or not self.peers.allows(peer_id, SCOPE, direction=direction):
            raise Conflict("Peer is not enabled for world guest travel")
        return peer

    async def destinations(self):
        projection = self.peers.snapshot()
        if projection.get("version") != 1:
            raise Conflict("Canonical peer projection is incompatible")
        rows = []
        for peer in projection.get("peers", []):
            if peer.get("enabled") is True and self.peers.allows(peer.get("id"), SCOPE, direction="send"):
                rows.append({"id": _identifier(peer.get("id")), "label": _name(peer.get("label")), "category": SCOPE})
        return {"schema_version": VERSION, "destinations": rows[:64]}

    def _replay(self, db, scope, request_id, body):
        _identifier(request_id)
        encoded = json.dumps(body, sort_keys=True, separators=(",", ":"))
        row = db.execute("SELECT input,result FROM world_guest_requests WHERE scope=? AND request_id=?", (scope, request_id)).fetchone()
        if row and row[0] != encoded:
            raise Conflict("Travel request identifier was reused with different input")
        return encoded, json.loads(row[1]) if row else None

    def _remember(self, db, scope, request_id, encoded, result):
        db.execute("INSERT INTO world_guest_requests VALUES(?,?,?,?)", (scope, request_id, encoded, json.dumps(result)))
        return result

    async def admit(self, verified_peer_id, body):
        peer = self._peer(verified_peer_id, "receive")
        if not isinstance(body, dict) or set(body) != {"world", "guest_name", "request_id"}:
            raise ValueError("Admission requires world, guest_name and request_id")
        world, guest_name, request_id = _identifier(body["world"]), _name(body["guest_name"]), _identifier(body["request_id"])
        await self.worlds.call(world)
        scope = "admit:" + peer["id"]
        with self.store.connection() as db:
            encoded, replay = self._replay(db, scope, request_id, body)
            if replay is not None:
                return replay
            active = db.execute("SELECT count(*) FROM world_guest_visits WHERE state IN ('invited','active') AND expires_at>?", (self.now(),)).fetchone()[0]
            if active >= 32:
                raise Conflict("World guest admission limit reached")
            ticket, visit_id = secrets.token_urlsafe(32), secrets.token_hex(16)
            expires_at = self.now() + 1800
            db.execute("INSERT INTO world_guest_visits VALUES(?,?,?,?,?,?,?,?,1)",
                       (visit_id, hashlib.sha256(ticket.encode()).hexdigest(), peer["id"], world, guest_name, "invited", self.now(), expires_at))
            return self._remember(db, scope, request_id, encoded, {"schema_version":VERSION, "visit_id":visit_id, "ticket":ticket, "expires_at":expires_at})

    async def depart(self, body):
        if not isinstance(body, dict) or set(body) != {"peer_id", "world", "guest_name", "request_id"}:
            raise ValueError("Departure requires peer_id, world, guest_name and request_id")
        peer = self._peer(body["peer_id"], "send")
        request = {"world":_identifier(body["world"]), "guest_name":_name(body["guest_name"]), "request_id":_identifier(body["request_id"])}
        result = await self.peers.post_signed(peer["id"], SCOPE, "/api/capabilities/experience/world-travel/federation/admit", request)
        if not isinstance(result, dict) or result.get("schema_version") != VERSION:
            raise Conflict("Destination returned an incompatible guest admission")
        ticket, visit_id = result.get("ticket"), result.get("visit_id")
        if not isinstance(ticket, str) or len(ticket) > 128 or not isinstance(visit_id, str):
            raise Conflict("Destination returned an invalid guest admission")
        _identifier(visit_id)
        expires_at = result.get("expires_at")
        if type(expires_at) is not int or expires_at <= self.now():
            raise Conflict("Destination returned an expired guest admission")
        return {"peer_id":peer["id"], "label":peer["label"], "visit_id":visit_id,
                "url":peer["endpoint"].rstrip("/") + "/#/capabilities/experience?guest=" + ticket,
                "expires_at":expires_at}

    def guest(self, ticket):
        if not isinstance(ticket, str) or not 32 <= len(ticket) <= 128:
            raise NotFound("Guest invitation not found")
        digest, now = hashlib.sha256(ticket.encode()).hexdigest(), self.now()
        with self.store.connection() as db:
            row = db.execute("SELECT id,peer_id,world,guest_name,state,expires_at,revision FROM world_guest_visits WHERE ticket_hash=?", (digest,)).fetchone()
            if not row:
                raise NotFound("Guest invitation not found")
            if row[5] <= now:
                db.execute("UPDATE world_guest_visits SET state='expired',revision=revision+1 WHERE id=? AND state IN ('invited','active')", (row[0],))
                raise NotFound("Guest invitation expired")
            if row[4] == "left":
                raise NotFound("Guest visit has ended")
            if row[4] == "invited":
                db.execute("UPDATE world_guest_visits SET state='active',revision=revision+1 WHERE id=?", (row[0],))
                row = (*row[:4], "active", row[5], row[6] + 1)
            return {"schema_version":VERSION, "visit_id":row[0], "peer_id":row[1], "world":row[2], "guest_name":row[3], "state":row[4], "expires_at":row[5], "revision":row[6]}

    def leave(self, ticket, body):
        if not isinstance(body, dict) or set(body) != {"revision", "request_id"} or type(body["revision"]) is not int:
            raise ValueError("Leave requires revision and request_id")
        _identifier(body["request_id"])
        if not isinstance(ticket, str) or not 32 <= len(ticket) <= 128:
            raise NotFound("Guest invitation not found")
        with self.store.connection() as db:
            row = db.execute("SELECT id,state,expires_at,revision FROM world_guest_visits WHERE ticket_hash=?", (hashlib.sha256(ticket.encode()).hexdigest(),)).fetchone()
            if not row or row[2] <= self.now():
                raise NotFound("Guest invitation expired")
            encoded, replay = self._replay(db, "leave:" + row[0], body["request_id"], body)
            if replay is not None:
                return replay
            if row[1] == "left":
                raise NotFound("Guest visit has ended")
            if row[3] != body["revision"]:
                raise Conflict("Guest visit revision changed")
            db.execute("UPDATE world_guest_visits SET state='left',revision=revision+1 WHERE id=?", (row[0],))
            return self._remember(db, "leave:" + row[0], body["request_id"], encoded,
                                  {"visit_id":row[0], "state":"left", "revision":row[3] + 1})
