import base64
import binascii
import hashlib
import json
import re
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from gideon.workspace.capabilities.platform.peers import PeerError

SCOPE = "media.assets"
MAX_BYTES = 16 * 1024 * 1024
MIMES = {
    "image/png": "image",
    "image/jpeg": "image",
    "image/webp": "image",
    "video/mp4": "video",
    "video/webm": "video",
}
IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,200}")
REQUEST = re.compile(r"[A-Za-z0-9_-]{1,100}")


class MediaShareError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


class MediaShares:
    def __init__(self, home, peers, artifacts):
        self.path = Path(home) / "capabilities/platform/media_shares.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.peers, self.artifacts = peers, artifacts
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS outbound(id TEXT PRIMARY KEY,request_id TEXT UNIQUE,peer_id TEXT,artifact_id TEXT,artifact_version INTEGER,sha256 TEXT,bytes INTEGER,mime TEXT,kind TEXT,status TEXT,revision INTEGER,remote_artifact_id TEXT,error TEXT,created_at REAL,updated_at REAL);
                CREATE TABLE IF NOT EXISTS inbound(sender TEXT,share_id TEXT,fingerprint TEXT,artifact_id TEXT,sha256 TEXT,status TEXT,received_at REAL,updated_at REAL,PRIMARY KEY(sender,share_id));
            """)

    def db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    @staticmethod
    def row(row):
        return dict(row)

    def list(self):
        with self.db() as db:
            outbound = [
                self.row(row)
                for row in db.execute(
                    "SELECT * FROM outbound ORDER BY created_at DESC LIMIT 100"
                )
            ]
            inbound = [
                self.row(row)
                for row in db.execute(
                    "SELECT * FROM inbound ORDER BY received_at DESC LIMIT 100"
                )
            ]
        return {
            "scope": SCOPE,
            "max_bytes": MAX_BYTES,
            "outbound": outbound,
            "inbound": inbound,
        }

    def get(self, identity):
        if not isinstance(identity, str) or not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise MediaShareError("Invalid media share identifier")
        with self.db() as db:
            row = db.execute(
                "SELECT * FROM outbound WHERE id=?", (identity,)
            ).fetchone()
        if row is None:
            raise MediaShareError("Media share not found", 404)
        return self.row(row)

    def source(self, body):
        if not isinstance(body, dict) or set(body) != {
            "request_id",
            "peer_id",
            "artifact_id",
            "artifact_version",
        }:
            raise MediaShareError(
                "Expected request_id, peer_id, artifact_id and artifact_version only"
            )
        if not isinstance(body["request_id"], str) or not REQUEST.fullmatch(
            body["request_id"]
        ):
            raise MediaShareError("Invalid request identifier")
        if not isinstance(body["peer_id"], str) or not body["peer_id"].startswith(
            "peer-"
        ):
            raise MediaShareError("Invalid peer identifier")
        if not isinstance(body["artifact_id"], str) or not IDENTIFIER.fullmatch(
            body["artifact_id"]
        ):
            raise MediaShareError("Invalid artifact identifier")
        if type(body["artifact_version"]) is not int or body["artifact_version"] < 1:
            raise MediaShareError("Invalid artifact version")
        try:
            if not self.peers.allows(body["peer_id"], SCOPE, "send"):
                raise MediaShareError("Peer policy denies media sharing", 403)
        except PeerError as exc:
            raise MediaShareError("Peer policy denies media sharing", 403) from exc
        artifact = self.artifacts.get(
            body["artifact_id"], version=body["artifact_version"]
        )
        raw = self.artifacts.raw_bytes(
            body["artifact_id"], version=body["artifact_version"]
        )
        if artifact is None or raw is None or artifact.kind not in ("image", "video"):
            raise MediaShareError("Selected canonical media version was not found", 404)
        data, mime = raw
        if not data or len(data) > MAX_BYTES:
            raise MediaShareError(
                "Selected media exceeds the 16 MiB sharing limit", 413
            )
        if MIMES.get(mime) != artifact.kind:
            raise MediaShareError("Selected media MIME and kind are unsupported")
        return artifact, data, mime

    async def share(self, body):
        artifact, data, mime = self.source(body)
        digest = hashlib.sha256(data).hexdigest()
        fingerprint = hashlib.sha256(
            json.dumps(body, sort_keys=True).encode()
        ).hexdigest()
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            prior = db.execute(
                "SELECT * FROM outbound WHERE request_id=?", (body["request_id"],)
            ).fetchone()
            if prior:
                record = self.row(prior)
                expected = hashlib.sha256(
                    json.dumps(
                        {
                            key: record[key]
                            for key in (
                                "request_id",
                                "peer_id",
                                "artifact_id",
                                "artifact_version",
                            )
                        },
                        sort_keys=True,
                    ).encode()
                ).hexdigest()
                if expected != fingerprint:
                    raise MediaShareError(
                        "Request identifier already belongs to another media share", 409
                    )
                if record["status"] in ("active", "revoked"):
                    return record
                identity = record["id"]
            else:
                identity, stamp = uuid4().hex, time.time()
                db.execute(
                    "INSERT INTO outbound VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        identity,
                        body["request_id"],
                        body["peer_id"],
                        body["artifact_id"],
                        body["artifact_version"],
                        digest,
                        len(data),
                        mime,
                        artifact.kind,
                        "sending",
                        1,
                        "",
                        "",
                        stamp,
                        stamp,
                    ),
                )
        payload = {
            "operation": "offer",
            "share_id": identity,
            "artifact": {
                "source_id": artifact.slug,
                "source_version": body["artifact_version"],
                "name": artifact.name,
                "kind": artifact.kind,
                "mime": mime,
                "sha256": digest,
                "bytes": len(data),
                "data": base64.b64encode(data).decode(),
            },
        }
        try:
            result = await self.peers.post_signed(
                body["peer_id"],
                SCOPE,
                "/api/capabilities/platform/media-shares/receive",
                payload,
            )
            if (
                result.get("share_id") != identity
                or result.get("sha256") != digest
                or not isinstance(result.get("artifact_id"), str)
            ):
                raise MediaShareError("Peer returned an invalid media receipt", 502)
            with self.db() as db:
                db.execute(
                    "UPDATE outbound SET status='active',remote_artifact_id=?,error='',updated_at=? WHERE id=?",
                    (result["artifact_id"], time.time(), identity),
                )
        except Exception as exc:
            with self.db() as db:
                db.execute(
                    "UPDATE outbound SET status='failed',error=?,updated_at=? WHERE id=?",
                    (str(exc)[:1000], time.time(), identity),
                )
            if isinstance(exc, MediaShareError):
                raise
            if isinstance(exc, PeerError):
                raise MediaShareError(str(exc), exc.status) from exc
            raise
        return self.get(identity)

    async def revoke(self, identity, revision):
        record = self.get(identity)
        if type(revision) is not int or revision != record["revision"]:
            raise MediaShareError("Media share revision changed", 409)
        if record["status"] == "revoked":
            return record
        if record["status"] != "active":
            raise MediaShareError("Only an active media share can be revoked", 409)
        result = await self.peers.post_signed(
            record["peer_id"],
            SCOPE,
            "/api/capabilities/platform/media-shares/receive",
            {"operation": "revoke", "share_id": identity, "sha256": record["sha256"]},
        )
        if result.get("share_id") != identity or result.get("status") != "revoked":
            raise MediaShareError("Peer returned an invalid revocation receipt", 502)
        with self.db() as db:
            db.execute(
                "UPDATE outbound SET status='revoked',revision=revision+1,error='',updated_at=? WHERE id=? AND revision=?",
                (time.time(), identity, revision),
            )
        return self.get(identity)

    def receive(self, sender, payload):
        if not isinstance(payload, dict) or payload.get("operation") not in (
            "offer",
            "revoke",
        ):
            raise MediaShareError("Invalid selective media operation")
        if not isinstance(sender, str) or not sender.startswith("peer-"):
            raise MediaShareError("Peer policy denies inbound media", 403)
        try:
            allowed = self.peers.allows(sender, SCOPE, "receive")
        except PeerError as exc:
            raise MediaShareError("Peer policy denies inbound media", 403) from exc
        if not allowed:
            raise MediaShareError("Peer policy denies inbound media", 403)
        share_id = payload.get("share_id")
        if not isinstance(share_id, str) or not re.fullmatch(r"[a-f0-9]{32}", share_id):
            raise MediaShareError("Invalid media share identifier")
        with self.db() as db:
            prior = db.execute(
                "SELECT * FROM inbound WHERE sender=? AND share_id=?",
                (sender, share_id),
            ).fetchone()
        if payload["operation"] == "revoke":
            if not prior:
                raise MediaShareError("Inbound media share not found", 404)
            record = self.row(prior)
            if payload.get("sha256") != record["sha256"]:
                raise MediaShareError("Revocation hash does not match", 409)
            if record["status"] != "revoked":
                self.artifacts.delete(record["artifact_id"])
                with self.db() as db:
                    db.execute(
                        "UPDATE inbound SET status='revoked',updated_at=? WHERE sender=? AND share_id=?",
                        (time.time(), sender, share_id),
                    )
            return {"share_id": share_id, "status": "revoked"}
        artifact = payload.get("artifact")
        required = {
            "source_id",
            "source_version",
            "name",
            "kind",
            "mime",
            "sha256",
            "bytes",
            "data",
        }
        if not isinstance(artifact, dict) or set(artifact) != required:
            raise MediaShareError("Invalid shared media artifact")
        if (
            not isinstance(artifact["source_id"], str)
            or not IDENTIFIER.fullmatch(artifact["source_id"])
            or type(artifact["source_version"]) is not int
            or artifact["source_version"] < 1
        ):
            raise MediaShareError("Invalid shared media source identity")
        if (
            not isinstance(artifact["name"], str)
            or not artifact["name"].strip()
            or len(artifact["name"]) > 200
            or MIMES.get(artifact["mime"]) != artifact["kind"]
        ):
            raise MediaShareError("Invalid shared media metadata")
        if (
            type(artifact["bytes"]) is not int
            or not 1 <= artifact["bytes"] <= MAX_BYTES
            or not isinstance(artifact["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"])
        ):
            raise MediaShareError("Invalid shared media size or hash")
        try:
            data = base64.b64decode(artifact["data"], validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise MediaShareError("Invalid shared media encoding") from exc
        if (
            len(data) != artifact["bytes"]
            or hashlib.sha256(data).hexdigest() != artifact["sha256"]
        ):
            raise MediaShareError(
                "Shared media bytes do not match their size or hash", 409
            )
        fingerprint = hashlib.sha256(
            json.dumps(
                {key: artifact[key] for key in required - {"data"}}, sort_keys=True
            ).encode()
            + data
        ).hexdigest()
        if prior:
            record = self.row(prior)
            if record["fingerprint"] != fingerprint:
                raise MediaShareError("Media share replay changed content", 409)
            if record["status"] == "revoked":
                raise MediaShareError("Revoked media share cannot be replayed", 409)
            return {
                "share_id": share_id,
                "artifact_id": record["artifact_id"],
                "sha256": record["sha256"],
                "status": "active",
            }
        slug = (
            "peer-media-"
            + hashlib.sha256((sender + ":" + share_id).encode()).hexdigest()[:40]
        )
        created = self.artifacts.create_binary(
            name=artifact["name"],
            slug=slug,
            data=data,
            mime=artifact["mime"],
            kind=artifact["kind"],
            source="import",
            tags=["peer-share", artifact["kind"]],
            description=f"Selective media share from {sender}",
        )
        stamp = time.time()
        with self.db() as db:
            db.execute(
                "INSERT INTO inbound VALUES(?,?,?,?,?,?,?,?)",
                (
                    sender,
                    share_id,
                    fingerprint,
                    created.slug,
                    artifact["sha256"],
                    "active",
                    stamp,
                    stamp,
                ),
            )
        return {
            "share_id": share_id,
            "artifact_id": created.slug,
            "sha256": artifact["sha256"],
            "status": "active",
        }
