"""Signed, durable dispatch of bounded media jobs to a direct peer."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from gideon.workspace.capabilities.media.sketches import SketchError
from gideon.workspace.capabilities.platform.peers import PeerStore

SCOPE = "media.remote_execution"
REMOTE_PATH = "/api/capabilities/platform/remote-media/federation"
TERMINAL = {"succeeded", "failed", "cancelled"}
STATES = TERMINAL | {"queued", "running", "cancel_requested"}


class RemoteMediaError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _public_job(job: dict) -> dict:
    result = job.get("result")
    if result is not None:
        if not isinstance(result, dict):
            result = None
        else:
            result = {
                key: result[key]
                for key in ("artifact_id", "version", "mime", "width", "height")
                if key in result
            }
    return {
        "id": job["id"],
        "operation": job["operation"],
        "status": job["status"],
        "attempt": job["attempt"],
        "state_revision": job["state_revision"],
        "result": result,
        "error": str(job.get("error") or "")[:500] or None,
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
    }


def _remote_job(value: object) -> dict:
    fields = {
        "id",
        "operation",
        "status",
        "attempt",
        "state_revision",
        "result",
        "error",
        "created_at",
        "updated_at",
    }
    if (
        not isinstance(value, dict)
        or set(value) != fields
        or not isinstance(value["id"], str)
        or value["operation"] != "image_generate"
        or value["status"] not in STATES
    ):
        raise RemoteMediaError("Peer returned invalid remote media state", 502)
    if (
        type(value["attempt"]) is not int
        or type(value["state_revision"]) is not int
        or value["attempt"] < 0
        or value["state_revision"] < 1
    ):
        raise RemoteMediaError("Peer returned invalid remote media revision", 502)
    if value["result"] is not None and (
        not isinstance(value["result"], dict)
        or set(value["result"]) - {"artifact_id", "version", "mime", "width", "height"}
    ):
        raise RemoteMediaError("Peer returned an invalid artifact reference", 502)
    return value


def _request(body: dict) -> dict:
    if not isinstance(body, dict) or set(body) != {"request_id", "operation", "input"}:
        raise RemoteMediaError(
            "Remote media request requires request_id, operation and input"
        )
    request_id = body["request_id"]
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 40:
        raise RemoteMediaError(
            "Remote media request ID must contain 1 to 40 characters"
        )
    if body["operation"] != "image_generate":
        raise RemoteMediaError("Only self-contained image generation can run remotely")
    value = body["input"]
    if not isinstance(value, dict) or set(value) - {"prompt", "size", "controls"}:
        raise RemoteMediaError(
            "Remote image input may contain only prompt, size and controls"
        )
    encoded = json.dumps(value, separators=(",", ":"))
    if len(encoded.encode()) > 16 * 1024:
        raise RemoteMediaError("Remote media input exceeds 16 KiB", 413)
    return {"request_id": request_id, "operation": body["operation"], "input": value}


class RemoteMedia:
    """Tracks remote admissions in the canonical MediaJobs database."""

    def __init__(self, jobs, peers: PeerStore):
        self.jobs, self.peers = jobs, peers
        with self.jobs.db() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS remote_media_executions(
                id TEXT PRIMARY KEY,request_id TEXT UNIQUE NOT NULL,fingerprint TEXT NOT NULL,
                peer_id TEXT NOT NULL,remote_job_id TEXT,remote_state_revision INTEGER,
                status TEXT NOT NULL,result TEXT,error TEXT,created_at TEXT NOT NULL,updated_at TEXT NOT NULL)"""
            )
            connection.execute("""CREATE TABLE IF NOT EXISTS remote_media_admissions(
                peer_id TEXT NOT NULL,request_id TEXT NOT NULL,job_id TEXT UNIQUE NOT NULL,
                PRIMARY KEY(peer_id,request_id))""")

    @staticmethod
    def _row(row) -> dict:
        if row is None:
            raise RemoteMediaError("Remote media execution not found", 404)
        keys = (
            "id",
            "request_id",
            "fingerprint",
            "peer_id",
            "remote_job_id",
            "remote_state_revision",
            "status",
            "result",
            "error",
            "created_at",
            "updated_at",
        )
        value = dict(zip(keys, row))
        value["result"] = json.loads(value["result"]) if value["result"] else None
        return value

    def get(self, execution_id: str) -> dict:
        with self.jobs.db() as connection:
            row = connection.execute(
                "SELECT * FROM remote_media_executions WHERE id=?", (execution_id,)
            ).fetchone()
        return self._row(row)

    def list(self) -> dict:
        with self.jobs.db() as connection:
            rows = connection.execute(
                "SELECT * FROM remote_media_executions ORDER BY rowid DESC LIMIT 100"
            ).fetchall()
        return {
            "items": [self._row(row) for row in rows],
            "peers": [
                peer
                for peer in self.peers.snapshot()["peers"]
                if peer["enabled"] and SCOPE in peer["send_categories"]
            ],
        }

    def _record(
        self,
        execution_id: str,
        request_id: str,
        fingerprint: str,
        peer_id: str,
        remote: dict,
    ) -> dict:
        stamp = _now()
        with self.jobs.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT INTO remote_media_executions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    execution_id,
                    request_id,
                    fingerprint,
                    peer_id,
                    remote["id"],
                    remote["state_revision"],
                    remote["status"],
                    (
                        json.dumps(remote.get("result"), separators=(",", ":"))
                        if remote.get("result") is not None
                        else None
                    ),
                    remote.get("error"),
                    stamp,
                    stamp,
                ),
            )
        return self.get(execution_id)

    def _update(self, execution_id: str, remote: dict) -> dict:
        with self.jobs.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "UPDATE remote_media_executions SET remote_state_revision=?,status=?,result=?,error=?,updated_at=? WHERE id=?",
                (
                    remote["state_revision"],
                    remote["status"],
                    (
                        json.dumps(remote.get("result"), separators=(",", ":"))
                        if remote.get("result") is not None
                        else None
                    ),
                    remote.get("error"),
                    _now(),
                    execution_id,
                ),
            )
        return self.get(execution_id)

    async def dispatch(self, peer_id: str, body: dict) -> dict:
        request = _request(body)
        fingerprint = _fingerprint({"peer_id": peer_id, **request})
        with self.jobs.db() as connection:
            row = connection.execute(
                "SELECT * FROM remote_media_executions WHERE request_id=?",
                (request["request_id"],),
            ).fetchone()
        if row:
            existing = self._row(row)
            if existing["fingerprint"] != fingerprint:
                raise RemoteMediaError(
                    "Remote media request ID conflicts with a prior execution", 409
                )
            return existing
        response = await self.peers.post_signed(
            peer_id, SCOPE, REMOTE_PATH + "/jobs", request
        )
        if (
            not isinstance(response, dict)
            or set(response) != {"accepted", "executor_peer_id", "job"}
            or response["accepted"] is not True
            or response["executor_peer_id"] != peer_id
        ):
            raise RemoteMediaError("Peer returned invalid remote media acceptance", 502)
        remote = _remote_job(response["job"])
        execution_id = str(uuid4())
        try:
            return self._record(
                execution_id, request["request_id"], fingerprint, peer_id, remote
            )
        except sqlite3.IntegrityError:
            with self.jobs.db() as connection:
                existing = connection.execute(
                    "SELECT * FROM remote_media_executions WHERE request_id=?",
                    (request["request_id"],),
                ).fetchone()
            value = self._row(existing)
            if value["fingerprint"] != fingerprint:
                raise RemoteMediaError(
                    "Remote media request ID conflicts with a prior execution", 409
                )
            return value

    async def refresh(self, execution_id: str) -> dict:
        execution = self.get(execution_id)
        response = await self.peers.post_signed(
            execution["peer_id"],
            SCOPE,
            REMOTE_PATH + "/status",
            {"job_id": execution["remote_job_id"]},
        )
        if not isinstance(response, dict) or set(response) != {"job"}:
            raise RemoteMediaError("Peer returned invalid remote media status", 502)
        return self._update(execution_id, _remote_job(response["job"]))

    async def cancel(self, execution_id: str, state_revision: int) -> dict:
        execution = self.get(execution_id)
        if execution["status"] in TERMINAL:
            return execution
        response = await self.peers.post_signed(
            execution["peer_id"],
            SCOPE,
            REMOTE_PATH + "/cancel",
            {"job_id": execution["remote_job_id"], "state_revision": state_revision},
        )
        if not isinstance(response, dict) or set(response) != {"job"}:
            raise RemoteMediaError(
                "Peer returned invalid remote media cancellation", 502
            )
        return self._update(execution_id, _remote_job(response["job"]))

    def accept(self, peer: dict, body: dict) -> dict:
        request = _request(body)
        namespaced = f"remote:{peer['id']}:{request['request_id']}"
        try:
            job = self.jobs.submit({**request, "request_id": namespaced})
        except SketchError as error:
            raise RemoteMediaError(str(error), error.status) from error
        with self.jobs.db() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO remote_media_admissions VALUES(?,?,?)",
                (peer["id"], request["request_id"], job["id"]),
            )
            admitted = connection.execute(
                "SELECT job_id FROM remote_media_admissions WHERE peer_id=? AND request_id=?",
                (peer["id"], request["request_id"]),
            ).fetchone()
        if not admitted or admitted[0] != job["id"]:
            raise RemoteMediaError(
                "Remote media admission conflicts with prior work", 409
            )
        return {
            "accepted": True,
            "executor_peer_id": self.peers.snapshot()["self"]["peer_id"],
            "job": _public_job(job),
        }

    def _admitted(self, peer: dict, body: dict, fields: set[str]) -> str:
        if (
            not isinstance(body, dict)
            or set(body) != fields
            or not isinstance(body.get("job_id"), str)
        ):
            raise RemoteMediaError("Remote request requires an admitted job ID")
        with self.jobs.db() as connection:
            row = connection.execute(
                "SELECT 1 FROM remote_media_admissions WHERE peer_id=? AND job_id=?",
                (peer["id"], body["job_id"]),
            ).fetchone()
        if row is None:
            raise RemoteMediaError("Peer does not own this remote media admission", 403)
        return body["job_id"]

    def status(self, peer: dict, body: dict) -> dict:
        job_id = self._admitted(peer, body, {"job_id"})
        try:
            return {"job": _public_job(self.jobs.get(job_id))}
        except SketchError as error:
            raise RemoteMediaError(str(error), error.status) from error

    def cancel_remote(self, peer: dict, body: dict) -> dict:
        job_id = self._admitted(peer, body, {"job_id", "state_revision"})
        try:
            return {
                "job": _public_job(
                    self.jobs.cancel(job_id, {"state_revision": body["state_revision"]})
                )
            }
        except SketchError as error:
            raise RemoteMediaError(str(error), error.status) from error


def create_remote_media(home: str | Path | None = None) -> RemoteMedia:
    from gideon.core.config.loader import config_dir
    from gideon.workspace.artifacts.native import NativeArtifactProvider
    from gideon.workspace.capabilities.media.jobs import MediaJobs
    from gideon.workspace.capabilities.media.sketches import SketchStore

    root = Path(home or config_dir())
    artifacts = NativeArtifactProvider(root / "artifacts")
    sketches = SketchStore(root / "capabilities/media/sketches.sqlite3", artifacts)
    jobs = MediaJobs(root / "capabilities/media/jobs.sqlite3", sketches)
    return RemoteMedia(jobs, PeerStore(root))
