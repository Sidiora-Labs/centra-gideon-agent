"""Approved commission-feedback propagation over the canonical signed peer transport."""
from __future__ import annotations

import hashlib
import json
import sqlite3
import base64
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from gideon.core.config.loader import config_dir

from .commissions import CommissionStore, digest, now_iso
from .store import CatalogError, identifier, integer, keys, text


SCOPE = "creative.commission_feedback"
RECEIVE_PATH = "/api/capabilities/creative/commission-feedback/receive"
SCHEMA_VERSION = 1


def _hash(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _bytes(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _output_matches(item: dict, output: dict) -> bool:
    return all(item.get(key) == value for key, value in output.items())


class PeerFeedbackStore:
    """Exports explicitly selected reactions and merges signed peer revisions."""

    def __init__(self, home=None, commissions=None, direction=None, peers=None):
        self.home = Path(home) if home is not None else config_dir()
        self.commissions = commissions or CommissionStore(self.home)
        self.direction = direction
        if peers is None:
            from gideon.workspace.capabilities.platform.peers import PeerStore
            peers = PeerStore(self.home)
        self.peers = peers
        self.path = self.home / "capabilities" / "creative" / "peer_feedback.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS creative_feedback_deliveries(
                    id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, sender TEXT NOT NULL,
                    reaction_id TEXT NOT NULL, revision INTEGER NOT NULL, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_feedback_sends(
                    id TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, peer_id TEXT NOT NULL,
                    receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS creative_feedback_outbox(
                    id TEXT PRIMARY KEY, peer_id TEXT NOT NULL, record TEXT NOT NULL, lineage TEXT NOT NULL);
                PRAGMA user_version=1;
            """)

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.path, timeout=15)
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _direction(self):
        if self.direction is None:
            from .direction import DirectionStore
            self.direction = DirectionStore(self.home)
        return self.direction

    def _reaction(self, commission_id: str, reaction_id: str) -> dict:
        commission_id, reaction_id = identifier(commission_id), identifier(reaction_id)
        self.commissions.get(commission_id)
        with self.commissions.db() as db:
            row = db.execute(
                "SELECT record FROM creative_commission_feedback WHERE id=? AND commission_id=?",
                (reaction_id, commission_id),
            ).fetchone()
        if row is None:
            raise CatalogError("Commission feedback not found", 404)
        return json.loads(row[0])

    def export(self, peer_id: str, commission_id: str, reaction_id: str, approval: dict) -> dict:
        """Build the minimal wire record after an exact caller-attested approval."""
        peer_id, reaction_id = identifier(peer_id), identifier(reaction_id)
        if not isinstance(approval, dict):
            raise CatalogError("Explicit peer-feedback approval is required", 403)
        keys(approval, {"decision", "peer_id", "reaction_id"})
        if approval.get("decision") != "approved" or approval.get("peer_id") != peer_id or approval.get("reaction_id") != reaction_id:
            raise CatalogError("Explicit peer-feedback approval does not match this delivery", 403)
        if not self.peers.allows(peer_id, SCOPE, "send"):
            raise CatalogError("Peer policy denies commission feedback", 403)
        reaction = self._reaction(commission_id, reaction_id)
        runs = self.commissions.runs(commission_id)["items"]
        run = next((item for item in runs if item["id"] == reaction["run_id"]), None)
        if run is None or not run.get("project_id"):
            raise CatalogError("Feedback run has no canonical direction project", 409)
        if not any(_output_matches(item, reaction["output"]) for item in run.get("outputs", [])):
            raise CatalogError("Feedback output no longer matches its run", 409)
        project = self._direction().get(run["project_id"])
        lineage = {
            "commission_id": commission_id,
            "run_id": run["id"],
            "project": {"id": project["id"], "revision": project["revision"]},
            "sources": [
                {"kind": item["kind"], "id": item["id"], "revision": item["revision"]}
                for item in project["sources"]
            ],
            "output": reaction["output"],
        }
        return self._sign(peer_id, reaction, lineage)

    def target(self, commission_id: str, run_id: str, output: dict) -> dict:
        commission_id, run_id = identifier(commission_id), identifier(run_id)
        normalized = self._output(output)
        self.commissions.get(commission_id)
        run = next((row for row in self.commissions.runs(commission_id)["items"] if row["id"] == run_id), None)
        if run is None or not run.get("project_id") or not any(
                _output_matches(item, normalized) for item in run.get("outputs", [])):
            raise CatalogError("Feedback target is not a linked commission output", 409)
        project = self._direction().get(run["project_id"])
        return {"commission_id": commission_id, "run_id": run_id,
                "project": {"id": project["id"], "revision": project["revision"]},
                "sources": [{"kind": item["kind"], "id": item["id"], "revision": item["revision"]}
                            for item in project["sources"]], "output": normalized}

    def prepare(self, peer_id: str, lineage: dict, body: dict, approval: dict) -> dict:
        peer_id = identifier(peer_id)
        self._approve_target(peer_id, lineage, approval)
        if not self.peers.allows(peer_id, SCOPE, "send"):
            raise CatalogError("Peer policy denies commission feedback", 403)
        self._validate_lineage(lineage)
        if not isinstance(body, dict): raise CatalogError("Peer-feedback reaction is required")
        keys(body, {"author", "rating", "note", "tags", "revision"})
        author, rating = identifier(body.get("author")), body.get("rating")
        if rating not in {"liked", "disliked"}: raise CatalogError("Feedback rating must be liked or disliked")
        tags = body.get("tags", [])
        if not isinstance(tags, list) or len(tags) > 20 or len(set(tags)) != len(tags):
            raise CatalogError("Feedback tags are invalid")
        tags = [text(item, 40, True) for item in tags]
        identity = "reaction-" + digest(":".join((lineage["commission_id"], lineage["run_id"],
                                                     lineage["output"]["artifact_id"], author)))[:48]
        with self.db() as db:
            row = db.execute("SELECT record FROM creative_feedback_outbox WHERE id=?", (identity,)).fetchone()
            old = json.loads(row[0]) if row else None
            if old and integer(body.get("revision")) != old["revision"]: raise CatalogError("Feedback changed; reload", 409)
            if not old and "revision" in body: raise CatalogError("New feedback does not accept a revision")
            stamp = now_iso()
            reaction = {"id": identity, "commission_id": lineage["commission_id"], "run_id": lineage["run_id"],
                        "author": author, "output": lineage["output"], "rating": rating,
                        "note": text(body.get("note", ""), 2000), "tags": tags,
                        "revision": old["revision"] + 1 if old else 1, "deleted": False, "deleted_at": None,
                        "created_at": old["created_at"] if old else stamp, "updated_at": stamp}
            db.execute("INSERT OR REPLACE INTO creative_feedback_outbox VALUES(?,?,?,?)",
                       (identity, peer_id, json.dumps(reaction, sort_keys=True), json.dumps(lineage, sort_keys=True)))
        return self._sign(peer_id, reaction, lineage)

    def revoke_prepared(self, peer_id: str, reaction_id: str, revision: int, approval: dict) -> dict:
        peer_id, reaction_id = identifier(peer_id), identifier(reaction_id)
        with self.db() as db:
            row = db.execute("SELECT record,lineage,peer_id FROM creative_feedback_outbox WHERE id=?", (reaction_id,)).fetchone()
            if row is None: raise CatalogError("Prepared feedback not found", 404)
            reaction, lineage = json.loads(row[0]), json.loads(row[1])
            self._approve_target(peer_id, lineage, approval)
            if row[2] != peer_id or integer(revision) != reaction["revision"]:
                raise CatalogError("Prepared feedback changed; reload", 409)
            reaction.update(revision=reaction["revision"] + 1, deleted=True,
                            deleted_at=now_iso(), updated_at=now_iso())
            db.execute("UPDATE creative_feedback_outbox SET record=? WHERE id=?",
                       (json.dumps(reaction, sort_keys=True), reaction_id))
        return self._sign(peer_id, reaction, lineage)

    @staticmethod
    def _output(output: dict) -> dict:
        if not isinstance(output, dict): raise CatalogError("Feedback output is required")
        keys(output, {"artifact_id", "artifact_version", "content_hash"})
        return {"artifact_id": identifier(output.get("artifact_id")),
                "artifact_version": integer(output.get("artifact_version"), 1),
                "content_hash": text(output.get("content_hash"), 64, True)}

    @staticmethod
    def _approve_target(peer_id: str, lineage: dict, approval: dict) -> None:
        if not isinstance(approval, dict): raise CatalogError("Explicit peer-feedback approval is required", 403)
        keys(approval, {"decision", "peer_id", "target_hash"})
        if (approval.get("decision") != "approved" or approval.get("peer_id") != peer_id or
                approval.get("target_hash") != _hash(lineage)):
            raise CatalogError("Explicit peer-feedback approval does not match this target", 403)

    def _sign(self, peer_id: str, reaction: dict, lineage: dict) -> dict:
        payload = {"schema_version": SCHEMA_VERSION, "reaction": reaction, "lineage": lineage}
        payload["delivery_id"] = "peer-feedback-" + digest(
            ":".join((peer_id, reaction["id"], str(reaction["revision"])))
        )[:48]
        private, identity = self.peers._identity()
        binding = {"version": 1, "sender": identity["peer_id"], "recipient": peer_id,
                   "scope": SCOPE, "payload_hash": _hash(payload)}
        binding["signature"] = _b64(private.sign(_bytes(binding)))
        payload["binding"] = binding
        return payload

    async def push(self, peer_id: str, commission_id: str, reaction_id: str, approval: dict) -> dict:
        payload = self.export(peer_id, commission_id, reaction_id, approval)
        return await self.push_prepared(peer_id, payload)

    async def push_prepared(self, peer_id: str, payload: dict) -> dict:
        peer_id = identifier(peer_id)
        if payload.get("binding", {}).get("recipient") != peer_id:
            raise CatalogError("Prepared feedback targets a different peer", 409)
        payload_hash = payload["binding"]["payload_hash"]
        with self.db() as db:
            row = db.execute(
                "SELECT payload_hash,receipt FROM creative_feedback_sends WHERE id=?", (payload["delivery_id"],)
            ).fetchone()
        if row:
            if row[0] != payload_hash:
                raise CatalogError("Peer-feedback delivery changed after receipt", 409)
            return json.loads(row[1])
        result = await self.peers.post_signed(peer_id, SCOPE, RECEIVE_PATH, payload)
        if not isinstance(result, dict) or result.get("delivery_id") != payload["delivery_id"] or result.get("payload_hash") != payload_hash:
            raise CatalogError("Peer returned an invalid feedback receipt", 502)
        with self.db() as db:
            db.execute(
                "INSERT OR REPLACE INTO creative_feedback_sends VALUES(?,?,?,?)",
                (payload["delivery_id"], payload_hash, peer_id, json.dumps(result, sort_keys=True)),
            )
        return result

    def receive_signed(self, envelope: dict) -> dict:
        if not isinstance(envelope, dict):
            raise CatalogError("Signed feedback envelope is required")
        keys(envelope, {"proof", "payload"})
        proof = envelope.get("proof")
        if not isinstance(proof, dict) or proof.get("scope") != SCOPE:
            raise CatalogError("Signed feedback scope is invalid", 403)
        try:
            peer = self.peers.verify_proof(proof)
        except ValueError as exc:
            raise CatalogError(str(exc), getattr(exc, "status", 401)) from exc
        return self.receive(peer["id"], envelope.get("payload"))

    def receive(self, sender: str, payload: dict) -> dict:
        sender = identifier(sender)
        if not self.peers.allows(sender, SCOPE, "receive"):
            raise CatalogError("Peer policy denies commission feedback", 403)
        payload_hash = self._verify_binding(sender, payload)
        reaction, lineage, delivery_id = self._validate(payload)
        with self.db() as db:
            prior = db.execute(
                "SELECT payload_hash,receipt FROM creative_feedback_deliveries WHERE id=?", (delivery_id,)
            ).fetchone()
            if prior:
                if prior[0] != payload_hash:
                    raise CatalogError("Feedback delivery was replayed with different content", 409)
                return json.loads(prior[1])

        self._verify_lineage(reaction, lineage)
        with self.commissions.db() as db:
            row = db.execute("SELECT record FROM creative_commission_feedback WHERE id=?", (reaction["id"],)).fetchone()
            current = json.loads(row[0]) if row else None
            if current and current["revision"] == reaction["revision"] and current != reaction:
                raise CatalogError("Feedback revision conflicts with local content", 409)
            if current is None or current["revision"] < reaction["revision"]:
                db.execute(
                    "INSERT OR REPLACE INTO creative_commission_feedback VALUES(?,?,?,?)",
                    (reaction["id"], reaction["commission_id"], reaction["run_id"], json.dumps(reaction, sort_keys=True)),
                )
                state = "applied"
            elif current == reaction:
                state = "already_current"
            else:
                state = "stale"
        receipt = {
            "delivery_id": delivery_id,
            "payload_hash": payload_hash,
            "reaction_id": reaction["id"],
            "revision": reaction["revision"],
            "state": state,
            "received_at": now_iso(),
        }
        with self.db() as db:
            db.execute(
                "INSERT INTO creative_feedback_deliveries VALUES(?,?,?,?,?,?)",
                (delivery_id, payload_hash, sender, reaction["id"], reaction["revision"], json.dumps(receipt, sort_keys=True)),
            )
        return receipt

    def _verify_binding(self, sender: str, payload: dict) -> str:
        if not isinstance(payload, dict) or not isinstance(payload.get("binding"), dict):
            raise CatalogError("Peer-feedback payload binding is required", 401)
        binding = payload["binding"]
        keys(binding, {"version", "sender", "recipient", "scope", "payload_hash", "signature"})
        unsigned = {key: value for key, value in payload.items() if key != "binding"}
        _, identity = self.peers._identity()
        if (binding.get("version") != 1 or binding.get("sender") != sender or
                binding.get("recipient") != identity["peer_id"] or binding.get("scope") != SCOPE or
                binding.get("payload_hash") != _hash(unsigned)):
            raise CatalogError("Peer-feedback payload binding does not match the signed delivery", 401)
        signed = {key: binding[key] for key in binding if key != "signature"}
        try:
            public_key = self.peers.get(sender)["public_key"]
            Ed25519PublicKey.from_public_bytes(_unb64(public_key)).verify(
                _unb64(binding.get("signature", "")), _bytes(signed)
            )
        except (InvalidSignature, TypeError, ValueError) as exc:
            raise CatalogError("Peer-feedback payload signature is invalid", 401) from exc
        return binding["payload_hash"]

    def _verify_lineage(self, reaction: dict, lineage: dict) -> None:
        try:
            self.commissions.get(reaction["commission_id"])
            run = next(
                row for row in self.commissions.runs(reaction["commission_id"])["items"]
                if row["id"] == reaction["run_id"]
            )
            project = self._direction().get(lineage["project"]["id"])
        except (CatalogError, StopIteration) as exc:
            raise CatalogError("Peer-feedback canonical lineage is unavailable", 409) from exc
        pins = [{"kind": item["kind"], "id": item["id"], "revision": item["revision"]}
                for item in project["sources"]]
        if (run.get("project_id") != lineage["project"]["id"] or
                project["revision"] != lineage["project"]["revision"] or pins != lineage["sources"] or
                not any(_output_matches(item, reaction["output"]) for item in run.get("outputs", []))):
            raise CatalogError("Peer-feedback canonical lineage does not match local records", 409)

    @classmethod
    def _validate_lineage(cls, lineage: dict) -> None:
        if not isinstance(lineage, dict): raise CatalogError("Peer-feedback lineage is required")
        keys(lineage, {"commission_id", "run_id", "project", "sources", "output"})
        identifier(lineage.get("commission_id")); identifier(lineage.get("run_id")); cls._output(lineage.get("output"))
        project, sources = lineage.get("project"), lineage.get("sources")
        if not isinstance(project, dict) or not isinstance(sources, list) or not sources:
            raise CatalogError("Peer-feedback project and source pins are required")
        keys(project, {"id", "revision"}); identifier(project.get("id")); integer(project.get("revision"), 1)
        for source in sources:
            if not isinstance(source, dict): raise CatalogError("Peer-feedback source pin is invalid")
            keys(source, {"kind", "id", "revision"})
            if source.get("kind") not in {"work", "series"}: raise CatalogError("Peer-feedback source kind is invalid")
            identifier(source.get("id")); integer(source.get("revision"), 1)

    @staticmethod
    def _validate(payload: dict) -> tuple[dict, dict, str]:
        if not isinstance(payload, dict):
            raise CatalogError("Peer-feedback payload is required")
        keys(payload, {"schema_version", "delivery_id", "reaction", "lineage", "binding"})
        if payload.get("schema_version") != SCHEMA_VERSION:
            raise CatalogError("Unsupported peer-feedback schema")
        delivery_id = identifier(payload.get("delivery_id"))
        reaction, lineage = payload.get("reaction"), payload.get("lineage")
        if not isinstance(reaction, dict) or not isinstance(lineage, dict):
            raise CatalogError("Peer-feedback record and lineage are required")
        keys(reaction, {"id", "commission_id", "run_id", "author", "output", "rating", "note", "tags",
                        "revision", "deleted", "deleted_at", "created_at", "updated_at"})
        keys(lineage, {"commission_id", "run_id", "project", "sources", "output"})
        output = reaction.get("output")
        if not isinstance(output, dict):
            raise CatalogError("Peer-feedback output is required")
        keys(output, {"artifact_id", "artifact_version", "content_hash"})
        normalized_output = {
            "artifact_id": identifier(output.get("artifact_id")),
            "artifact_version": integer(output.get("artifact_version"), 1),
            "content_hash": text(output.get("content_hash"), 64, True),
        }
        commission_id = identifier(reaction.get("commission_id"))
        run_id, author = identifier(reaction.get("run_id")), identifier(reaction.get("author"))
        expected_id = "reaction-" + digest(":".join((commission_id, run_id, normalized_output["artifact_id"], author)))[:48]
        if reaction.get("id") != expected_id or reaction.get("rating") not in {"liked", "disliked"}:
            raise CatalogError("Peer-feedback identity or rating is invalid")
        if type(reaction.get("deleted")) is not bool or integer(reaction.get("revision"), 1) < 1:
            raise CatalogError("Peer-feedback revision or tombstone is invalid")
        tags = reaction.get("tags")
        if not isinstance(tags, list) or len(tags) > 20 or len(set(tags)) != len(tags):
            raise CatalogError("Peer-feedback tags are invalid")
        for tag in tags:
            text(tag, 40, True)
        text(reaction.get("note", ""), 2000)
        project, sources = lineage.get("project"), lineage.get("sources")
        if not isinstance(project, dict) or not isinstance(sources, list) or not sources:
            raise CatalogError("Peer-feedback project and source pins are required")
        keys(project, {"id", "revision"}); identifier(project.get("id")); integer(project.get("revision"), 1)
        for source in sources:
            if not isinstance(source, dict):
                raise CatalogError("Peer-feedback source pin is invalid")
            keys(source, {"kind", "id", "revision"})
            if source.get("kind") not in {"work", "series"}:
                raise CatalogError("Peer-feedback source kind is invalid")
            identifier(source.get("id")); integer(source.get("revision"), 1)
        if lineage.get("commission_id") != commission_id or lineage.get("run_id") != run_id or lineage.get("output") != normalized_output:
            raise CatalogError("Peer-feedback lineage does not match its reaction")
        return reaction, lineage, delivery_id
