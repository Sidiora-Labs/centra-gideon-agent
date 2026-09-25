"""Machine-local peer identity, directional category policy, and signed transport."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from gideon.core.config.loader import config_dir
from gideon.security.net.client import fetch
from gideon.security.net.policy import sync_egress_policy

CATEGORIES = (
    "workspace.records",
    "experience.world_guest",
    "experience.world_presence",
    "knowledge.records",
    "media.assets",
    "creative.catalog",
    "identity.goals",
    "identity.profile",
    "music.library",
    "platform.usage",
)
_CATEGORY = re.compile(r"[a-z][a-z0-9_]{1,31}\.[a-z][a-z0-9_]{1,63}")
_ID = re.compile(r"peer-[0-9a-f]{32}")
_NONCE = re.compile(r"[A-Za-z0-9_-]{22,64}")
_PROOF_FIELDS = {"version", "sender", "recipient", "scope", "expires_at", "nonce", "signature"}


class PeerError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _canonical(proof: dict) -> bytes:
    return json.dumps({key: proof[key] for key in sorted(_PROOF_FIELDS - {"signature"})}, separators=(",", ":"), sort_keys=True).encode()


class PeerStore:
    def __init__(self, home: str | Path | None = None):
        self.home = Path(home or config_dir())
        self.root = self.home / "capabilities/platform"
        self.key_path = self.root / "identity.key"
        self.db_path = self.root / "peers.sqlite3"

    def _identity(self) -> tuple[Ed25519PrivateKey, dict]:
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.key_path.exists():
            private = Ed25519PrivateKey.generate()
            raw_private = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
            public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            document = {"version": 1, "private_key": _b64(raw_private), "public_key": _b64(public)}
            temporary = self.key_path.with_suffix(f".tmp-{os.getpid()}-{secrets.token_hex(4)}")
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "w") as stream:
                    json.dump(document, stream, separators=(",", ":"))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.key_path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        if self.key_path.stat().st_mode & 0o077:
            raise PeerError("Peer identity key permissions must be 0600", 503)
        document = json.loads(self.key_path.read_text())
        private = Ed25519PrivateKey.from_private_bytes(_unb64(document["private_key"]))
        public = _b64(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
        if document != {"version": 1, "private_key": document["private_key"], "public_key": public}:
            raise PeerError("Peer identity key is invalid", 503)
        return private, {"peer_id": "peer-" + hashlib.sha256(_unb64(public)).hexdigest()[:32], "public_key": public}

    def _connect(self) -> sqlite3.Connection:
        self.root.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS peers(id TEXT PRIMARY KEY,label TEXT NOT NULL,endpoint TEXT NOT NULL,public_key TEXT NOT NULL,enabled INTEGER NOT NULL,send_categories TEXT NOT NULL,receive_categories TEXT NOT NULL,revision INTEGER NOT NULL,last_probe TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS nonces(sender TEXT NOT NULL,nonce TEXT NOT NULL,expires_at INTEGER NOT NULL,PRIMARY KEY(sender,nonce));
        """)
        return connection

    @staticmethod
    def _row(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["enabled"] = bool(value["enabled"])
        value["send_categories"] = json.loads(value["send_categories"])
        value["receive_categories"] = json.loads(value["receive_categories"])
        return value

    def snapshot(self) -> dict:
        _, identity = self._identity()
        with self._connect() as connection:
            peers = [self._row(row) for row in connection.execute("SELECT * FROM peers ORDER BY label,id")]
        return {"version": 1, "self": identity, "categories": list(CATEGORIES), "peers": peers}

    def get(self, peer_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM peers WHERE id=?", (peer_id,)).fetchone()
        if row is None:
            raise PeerError("Peer not found", 404)
        return self._row(row)

    def put(self, peer_id: str, body: dict) -> dict:
        fields = {"label", "endpoint", "public_key", "enabled", "send_categories", "receive_categories", "revision"}
        if not _ID.fullmatch(peer_id) or not isinstance(body, dict) or set(body) != fields:
            raise PeerError("Invalid peer record")
        label, endpoint, public_key = body["label"], body["endpoint"], body["public_key"]
        if not isinstance(label, str) or not label.strip() or len(label.strip()) > 100:
            raise PeerError("Peer label is required")
        parsed = urlparse(endpoint) if isinstance(endpoint, str) else None
        if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise PeerError("Peer endpoint must be an http(s) origin")
        if parsed.scheme != "https" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise PeerError("Peer endpoint requires HTTPS outside loopback")
        try:
            public_bytes = _unb64(public_key)
            Ed25519PublicKey.from_public_bytes(public_bytes)
        except (TypeError, ValueError):
            raise PeerError("Peer public key must be Ed25519 base64url")
        if peer_id != "peer-" + hashlib.sha256(public_bytes).hexdigest()[:32]:
            raise PeerError("Peer ID does not match its public key")
        categories = []
        for key in ("send_categories", "receive_categories"):
            value = body[key]
            if not isinstance(value, list) or len(value) > 64 or len(set(value)) != len(value) or any(not isinstance(item, str) or not _CATEGORY.fullmatch(item) for item in value):
                raise PeerError("Peer categories must be unique qualified names")
            categories.append(json.dumps(sorted(value), separators=(",", ":")))
        if type(body["enabled"]) is not bool or type(body["revision"]) is not int or body["revision"] < 0:
            raise PeerError("Invalid enabled state or revision")
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM peers WHERE id=?", (peer_id,)).fetchone()
            if body["revision"] != (current["revision"] if current else 0):
                raise PeerError("Peer changed; refresh before saving", 409)
            created = current["created_at"] if current else now
            revision = body["revision"] + 1
            connection.execute("INSERT INTO peers VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET label=excluded.label,endpoint=excluded.endpoint,public_key=excluded.public_key,enabled=excluded.enabled,send_categories=excluded.send_categories,receive_categories=excluded.receive_categories,revision=excluded.revision,updated_at=excluded.updated_at", (peer_id, label.strip(), endpoint.rstrip("/"), public_key, int(body["enabled"]), *categories, revision, current["last_probe"] if current else None, created, now))
        return self.get(peer_id)

    def delete(self, peer_id: str, revision: int) -> None:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT revision FROM peers WHERE id=?", (peer_id,)).fetchone()
            if row is None:
                raise PeerError("Peer not found", 404)
            if type(revision) is not int or revision != row["revision"]:
                raise PeerError("Peer changed; refresh before deleting", 409)
            connection.execute("DELETE FROM peers WHERE id=?", (peer_id,))

    def allows(self, peer_id: str, scope: str, direction: str = "send") -> bool:
        peer = self.get(peer_id)
        if direction not in {"send", "receive"}:
            raise PeerError("Invalid peer policy direction")
        return peer["enabled"] and scope in peer[f"{direction}_categories"]

    def create_proof(self, peer_id: str, scope: str, ttl_seconds: int = 60) -> dict:
        if not self.allows(peer_id, scope, "send"):
            raise PeerError("Peer policy denies this outbound scope", 403)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise PeerError("Proof lifetime must be 1 to 300 seconds")
        private, identity = self._identity()
        proof = {"version": 1, "sender": identity["peer_id"], "recipient": peer_id, "scope": scope, "expires_at": int(time.time()) + ttl_seconds, "nonce": _b64(secrets.token_bytes(18))}
        proof["signature"] = _b64(private.sign(_canonical(proof)))
        return proof

    def verify_proof(self, proof: dict, now: int | None = None) -> dict:
        if not isinstance(proof, dict) or set(proof) != _PROOF_FIELDS or proof.get("version") != 1:
            raise PeerError("Invalid peer proof", 401)
        _, identity = self._identity()
        stamp = int(time.time()) if now is None else now
        if proof["recipient"] != identity["peer_id"] or not isinstance(proof["expires_at"], int) or not stamp < proof["expires_at"] <= stamp + 300 or not isinstance(proof["nonce"], str) or not _NONCE.fullmatch(proof["nonce"]):
            raise PeerError("Peer proof recipient, expiry, or nonce is invalid", 401)
        try:
            peer = self.get(proof["sender"])
            if not self.allows(peer["id"], proof["scope"], "receive"):
                raise PeerError("Peer policy denies this inbound scope", 403)
            Ed25519PublicKey.from_public_bytes(_unb64(peer["public_key"])).verify(_unb64(proof["signature"]), _canonical(proof))
        except (InvalidSignature, TypeError, ValueError) as exc:
            if isinstance(exc, PeerError):
                raise
            raise PeerError("Peer proof signature is invalid", 401)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("DELETE FROM nonces WHERE expires_at<=?", (stamp,))
            try:
                connection.execute("INSERT INTO nonces VALUES(?,?,?)", (peer["id"], proof["nonce"], proof["expires_at"]))
            except sqlite3.IntegrityError:
                raise PeerError("Peer proof nonce was already used", 409)
        return peer

    async def post_signed(self, peer_id: str, scope: str, path: str, payload: dict) -> dict:
        peer = self.get(peer_id)
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//"):
            raise PeerError("Peer path must be absolute")
        proof = self.create_proof(peer_id, scope)
        data = json.dumps({"proof": proof, "payload": payload}, separators=(",", ":")).encode()
        response = await fetch(urljoin(peer["endpoint"] + "/", path.lstrip("/")), policy=sync_egress_policy(peer["endpoint"]), method="POST", headers={"Content-Type": "application/json"}, data=data)
        if response.status < 200 or response.status >= 300 or response.truncated:
            raise PeerError(f"Peer transport returned HTTP {response.status}", 502)
        try:
            result = json.loads(response.body)
        except json.JSONDecodeError:
            raise PeerError("Peer transport returned invalid JSON", 502)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with self._connect() as connection:
            connection.execute("UPDATE peers SET last_probe=?,updated_at=? WHERE id=?", (now, now, peer_id))
        return result
