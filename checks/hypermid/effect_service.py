from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import socketserver
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any


TERMINAL_STATES = {"committed", "not_started", "unknown"}


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=FULL")
    return connection


def _initialize_service(path: Path) -> None:
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS attempts (
                effect_id TEXT PRIMARY KEY,
                input_digest TEXT NOT NULL,
                attempt_count INTEGER NOT NULL CHECK (attempt_count > 0)
            );
            CREATE TABLE IF NOT EXISTS effects (
                effect_id TEXT PRIMARY KEY,
                input_digest TEXT NOT NULL,
                result_digest TEXT NOT NULL,
                result TEXT NOT NULL,
                commit_count INTEGER NOT NULL CHECK (commit_count = 1)
            );
            """
        )


def _initialize_ledger(path: Path) -> None:
    with _connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS effects (
                effect_id TEXT PRIMARY KEY,
                input_digest TEXT NOT NULL,
                state TEXT NOT NULL,
                result_digest TEXT,
                result TEXT,
                role TEXT NOT NULL,
                updated_ns INTEGER NOT NULL
            );
            """
        )


def _durable_marker(root: Path, component: str, stage: str, pause_at: str | None) -> None:
    root.mkdir(parents=True, exist_ok=True)
    target = root / f"{component}-{stage}.ready"
    temporary = root / f".{component}-{stage}.{os.getpid()}.tmp"
    with temporary.open("wb") as handle:
        handle.write(f"{os.getpid()}\n".encode())
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    if pause_at == stage:
        release = root / f"{component}-{stage}.continue"
        while not release.exists():
            time.sleep(0.01)


def _validate_effect(effect_id: str, input_digest: str) -> None:
    if not effect_id or len(effect_id) > 160:
        raise ValueError("invalid effect_id")
    if len(input_digest) != 64 or any(character not in "0123456789abcdef" for character in input_digest):
        raise ValueError("invalid input_digest")


class EffectServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(self, socket_path: Path, database: Path, markers: Path, pause_at: str | None):
        self.database = database
        self.markers = markers
        self.pause_at = pause_at
        socket_path.unlink(missing_ok=True)
        super().__init__(str(socket_path), EffectHandler)
        os.chmod(socket_path, 0o600)


class EffectHandler(socketserver.StreamRequestHandler):
    server: EffectServer

    def handle(self) -> None:
        raw = self.rfile.readline(1_048_577)
        if not raw or len(raw) > 1_048_576:
            return
        try:
            request = json.loads(raw)
            response = self._dispatch(request)
        except Exception as error:
            response = {"state": "refused", "error": type(error).__name__}
        self.wfile.write(json.dumps(response, sort_keys=True, separators=(",", ":")).encode() + b"\n")
        self.wfile.flush()

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        operation = request.get("operation")
        if operation == "ping":
            return {"state": "ready"}
        effect_id = str(request.get("effect_id", ""))
        if operation == "status":
            return _service_status(self.server.database, effect_id)
        if operation != "apply":
            raise ValueError("unsupported operation")
        input_digest = str(request.get("input_digest", ""))
        result = str(request.get("result", ""))
        _validate_effect(effect_id, input_digest)
        with _connect(self.server.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT input_digest, attempt_count FROM attempts WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
            if existing is not None and existing[0] != input_digest:
                connection.execute("ROLLBACK")
                return {"state": "refused", "error": "divergent_effect"}
            if existing is None:
                connection.execute(
                    "INSERT INTO attempts(effect_id, input_digest, attempt_count) VALUES (?, ?, 1)",
                    (effect_id, input_digest),
                )
            else:
                connection.execute(
                    "UPDATE attempts SET attempt_count = attempt_count + 1 WHERE effect_id = ?",
                    (effect_id,),
                )
            connection.execute("COMMIT")
        _durable_marker(
            self.server.markers,
            "service",
            "during_dispatch",
            self.server.pause_at,
        )
        result_digest = hashlib.sha256(result.encode()).hexdigest()
        with _connect(self.server.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT input_digest, result_digest, result FROM effects WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
            if existing is not None:
                if existing[0] != input_digest:
                    connection.execute("ROLLBACK")
                    return {"state": "refused", "error": "divergent_effect"}
                connection.execute("COMMIT")
                return {
                    "state": "committed",
                    "effect_id": effect_id,
                    "result_digest": existing[1],
                    "result": existing[2],
                }
            connection.execute(
                "INSERT INTO effects(effect_id, input_digest, result_digest, result, commit_count) "
                "VALUES (?, ?, ?, ?, 1)",
                (effect_id, input_digest, result_digest, result),
            )
            connection.execute("COMMIT")
        return {
            "state": "committed",
            "effect_id": effect_id,
            "result_digest": result_digest,
            "result": result,
        }


def _service_status(database: Path, effect_id: str) -> dict[str, Any]:
    with _connect(database) as connection:
        row = connection.execute(
            "SELECT input_digest, result_digest, result, commit_count FROM effects WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
    if row is None:
        return {"state": "not_started", "effect_id": effect_id}
    return {
        "state": "committed",
        "effect_id": effect_id,
        "input_digest": row[0],
        "result_digest": row[1],
        "result": row[2],
        "commit_count": row[3],
    }


def _service_request(socket_path: Path, request: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(request, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2.0)
        client.connect(str(socket_path))
        client.sendall(payload)
        response = bytearray()
        while not response.endswith(b"\n"):
            chunk = client.recv(65_536)
            if not chunk:
                raise ConnectionError("effect service disconnected before acknowledgement")
            response.extend(chunk)
            if len(response) > 1_048_576:
                raise ValueError("effect service response exceeds bound")
    return json.loads(response)


def _ledger_read(path: Path, effect_id: str) -> dict[str, Any] | None:
    _initialize_ledger(path)
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT input_digest, state, result_digest, result, role, updated_ns "
            "FROM effects WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
    if row is None:
        return None
    return {
        "effect_id": effect_id,
        "input_digest": row[0],
        "state": row[1],
        "result_digest": row[2],
        "result": row[3],
        "role": row[4],
        "updated_ns": row[5],
    }


def _ledger_write(
    path: Path,
    effect_id: str,
    input_digest: str,
    state: str,
    role: str,
    *,
    result_digest: str | None = None,
    result: str | None = None,
) -> None:
    _initialize_ledger(path)
    with _connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute(
            "SELECT input_digest FROM effects WHERE effect_id = ?", (effect_id,)
        ).fetchone()
        if existing is not None and existing[0] != input_digest:
            connection.execute("ROLLBACK")
            raise ValueError("effect id was reused with another digest")
        connection.execute(
            "INSERT INTO effects(effect_id, input_digest, state, result_digest, result, role, updated_ns) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(effect_id) DO UPDATE SET state=excluded.state, "
            "result_digest=excluded.result_digest, result=excluded.result, role=excluded.role, "
            "updated_ns=excluded.updated_ns",
            (effect_id, input_digest, state, result_digest, result, role, time.time_ns()),
        )
        connection.execute("COMMIT")


def _recover(ledger: Path, socket_path: Path, effect_id: str, input_digest: str, role: str) -> dict[str, Any]:
    record = _ledger_read(ledger, effect_id)
    if record is None:
        return {"effect_id": effect_id, "state": "not_started", "source": "no_intent"}
    if record["input_digest"] != input_digest:
        raise ValueError("effect id was reused with another digest")
    if record["state"] == "committed":
        return {**record, "source": "ledger"}
    if record["state"] == "intent":
        _ledger_write(ledger, effect_id, input_digest, "not_started", role)
        return {"effect_id": effect_id, "state": "not_started", "source": "intent_only"}
    try:
        status = _service_request(
            socket_path,
            {"operation": "status", "effect_id": effect_id},
        )
    except (ConnectionError, FileNotFoundError, OSError, TimeoutError):
        _ledger_write(ledger, effect_id, input_digest, "unknown", role)
        return {"effect_id": effect_id, "state": "unknown", "source": "service_unavailable"}
    if status["state"] == "committed":
        if status.get("input_digest") != input_digest:
            raise ValueError("service effect digest differs from ledger")
        _ledger_write(
            ledger,
            effect_id,
            input_digest,
            "committed",
            role,
            result_digest=status["result_digest"],
            result=status["result"],
        )
        return {**status, "source": "service_reconciliation"}
    if status["state"] == "not_started":
        _ledger_write(ledger, effect_id, input_digest, "not_started", role)
        return {"effect_id": effect_id, "state": "not_started", "source": "service_proof"}
    _ledger_write(ledger, effect_id, input_digest, "unknown", role)
    return {"effect_id": effect_id, "state": "unknown", "source": "service_ambiguous"}


def _run_effect(arguments: argparse.Namespace) -> int:
    ledger = Path(arguments.ledger)
    socket_path = Path(arguments.socket)
    markers = Path(arguments.markers)
    _validate_effect(arguments.effect_id, arguments.input_digest)
    existing = _ledger_read(ledger, arguments.effect_id)
    if existing is not None:
        if existing["input_digest"] != arguments.input_digest:
            raise ValueError("effect id was reused with another digest")
        if existing["state"] == "committed":
            print(json.dumps(existing, sort_keys=True))
            return 0
        if existing["state"] in {"dispatched", "unknown"}:
            print(
                json.dumps(
                    {
                        "effect_id": arguments.effect_id,
                        "state": "unknown",
                        "source": "retry_blocked",
                    },
                    sort_keys=True,
                )
            )
            return 4
    _durable_marker(markers, arguments.role, "before_intent", arguments.pause_at)
    _ledger_write(
        ledger,
        arguments.effect_id,
        arguments.input_digest,
        "intent",
        arguments.role,
    )
    _durable_marker(markers, arguments.role, "after_intent", arguments.pause_at)
    _ledger_write(
        ledger,
        arguments.effect_id,
        arguments.input_digest,
        "dispatched",
        arguments.role,
    )
    try:
        acknowledgement = _service_request(
            socket_path,
            {
                "operation": "apply",
                "effect_id": arguments.effect_id,
                "input_digest": arguments.input_digest,
                "result": arguments.result,
            },
        )
    except (ConnectionError, FileNotFoundError, OSError, TimeoutError):
        _ledger_write(
            ledger,
            arguments.effect_id,
            arguments.input_digest,
            "unknown",
            arguments.role,
        )
        print(json.dumps({"effect_id": arguments.effect_id, "state": "unknown"}, sort_keys=True))
        return 3
    if acknowledgement.get("state") != "committed":
        _ledger_write(
            ledger,
            arguments.effect_id,
            arguments.input_digest,
            "unknown",
            arguments.role,
        )
        print(json.dumps({"effect_id": arguments.effect_id, "state": "unknown"}, sort_keys=True))
        return 3
    _durable_marker(markers, arguments.role, "after_external_ack", arguments.pause_at)
    _durable_marker(markers, arguments.role, "before_result_commit", arguments.pause_at)
    _ledger_write(
        ledger,
        arguments.effect_id,
        arguments.input_digest,
        "committed",
        arguments.role,
        result_digest=acknowledgement["result_digest"],
        result=acknowledgement["result"],
    )
    _durable_marker(markers, arguments.role, "after_result_commit", arguments.pause_at)
    print(json.dumps(acknowledgement, sort_keys=True))
    return 0


def _inspect(database: Path, ledger: Path, effect_id: str) -> dict[str, Any]:
    _initialize_service(database)
    service = _service_status(database, effect_id)
    with _connect(database) as connection:
        attempt = connection.execute(
            "SELECT attempt_count FROM attempts WHERE effect_id = ?", (effect_id,)
        ).fetchone()
        committed_rows = connection.execute(
            "SELECT COUNT(*), COALESCE(SUM(commit_count), 0) FROM effects WHERE effect_id = ?",
            (effect_id,),
        ).fetchone()
    return {
        "service": service,
        "attempt_count": 0 if attempt is None else attempt[0],
        "committed_rows": committed_rows[0],
        "commit_count": committed_rows[1],
        "ledger": _ledger_read(ledger, effect_id),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    serve = subparsers.add_parser("serve")
    serve.add_argument("--socket", required=True)
    serve.add_argument("--database", required=True)
    serve.add_argument("--markers", required=True)
    serve.add_argument("--pause-at")
    run = subparsers.add_parser("run")
    run.add_argument("--socket", required=True)
    run.add_argument("--ledger", required=True)
    run.add_argument("--markers", required=True)
    run.add_argument("--role", choices=["daemon", "adapter", "sandbox"], required=True)
    run.add_argument("--effect-id", required=True)
    run.add_argument("--input-digest", required=True)
    run.add_argument("--result", required=True)
    run.add_argument("--pause-at")
    recover = subparsers.add_parser("recover")
    recover.add_argument("--socket", required=True)
    recover.add_argument("--ledger", required=True)
    recover.add_argument("--role", choices=["daemon", "adapter", "sandbox"], required=True)
    recover.add_argument("--effect-id", required=True)
    recover.add_argument("--input-digest", required=True)
    inspect = subparsers.add_parser("inspect")
    inspect.add_argument("--database", required=True)
    inspect.add_argument("--ledger", required=True)
    inspect.add_argument("--effect-id", required=True)
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    if arguments.command == "serve":
        socket_path = Path(arguments.socket)
        database = Path(arguments.database)
        _initialize_service(database)
        with EffectServer(
            socket_path,
            database,
            Path(arguments.markers),
            arguments.pause_at,
        ) as server:
            server.serve_forever(poll_interval=0.05)
        return 0
    if arguments.command == "run":
        return _run_effect(arguments)
    if arguments.command == "recover":
        result = _recover(
            Path(arguments.ledger),
            Path(arguments.socket),
            arguments.effect_id,
            arguments.input_digest,
            arguments.role,
        )
        if result["state"] not in TERMINAL_STATES:
            raise RuntimeError("recovery returned a non-terminal effect state")
        print(json.dumps(result, sort_keys=True))
        return 0
    if arguments.command == "inspect":
        print(
            json.dumps(
                _inspect(
                    Path(arguments.database),
                    Path(arguments.ledger),
                    arguments.effect_id,
                ),
                sort_keys=True,
            )
        )
        return 0
    raise AssertionError(arguments.command)


if __name__ == "__main__":
    raise SystemExit(main())
