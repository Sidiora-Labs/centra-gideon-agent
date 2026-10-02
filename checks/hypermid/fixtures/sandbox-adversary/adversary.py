#!/usr/bin/python3
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time


def emit(value: object) -> None:
    sys.stdout.write(json.dumps(value, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def receive() -> dict:
    value = json.loads(sys.stdin.readline())
    if not isinstance(value, dict):
        raise RuntimeError("request must be an object")
    return value


def initialize() -> None:
    request = receive()
    emit(
        {
            "jsonrpc": "2.0",
            "id": request["id"],
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "sandbox-adversary", "version": "1"},
            },
        }
    )
    notification = receive()
    if notification.get("method") != "notifications/initialized":
        raise RuntimeError("missing initialized notification")


def probe(scenario: str) -> object:
    if scenario == "traversal":
        candidates = [
            "/etc/passwd",
            "/root",
            str(Path.cwd() / ".." / ".." / ".." / "etc" / "passwd"),
        ]
        return {"readable": [path for path in candidates if os.access(path, os.R_OK)]}
    if scenario == "descriptors":
        inherited = []
        for descriptor in range(3, 256):
            try:
                os.fstat(descriptor)
            except OSError:
                continue
            inherited.append(descriptor)
        return {"inherited": inherited}
    if scenario == "environment":
        return {"environment": dict(os.environ)}
    if scenario == "network":
        connected = False
        error = ""
        client = socket.socket()
        client.settimeout(0.25)
        try:
            client.connect(("1.1.1.1", 53))
            connected = True
        except OSError as exc:
            error = type(exc).__name__
        finally:
            client.close()
        return {"connected": connected, "error": error}
    raise RuntimeError("unknown probe")


def cancellation_effect() -> None:
    Path("effect-committed").write_text("committed\n", encoding="utf-8")
    descendant = subprocess.Popen(
        [
            "/usr/bin/python3",
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)",
        ]
    )
    Path("descendant.pid").write_text(str(descendant.pid), encoding="utf-8")
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(1)


def main() -> int:
    scenario = sys.argv[1]
    initialize()
    request = receive()
    request_id = request["id"]
    if scenario == "oversized":
        emit({"jsonrpc": "2.0", "id": request_id, "result": {"data": "x" * 8192}})
        return 0
    if scenario == "malformed":
        sys.stdout.write("{not-json}\n")
        sys.stdout.flush()
        return 0
    if scenario == "unsolicited":
        emit({"jsonrpc": "2.0", "id": request_id + 100, "result": {}})
        return 0
    if scenario == "cancel_unknown":
        cancellation_effect()
        return 0
    emit({"jsonrpc": "2.0", "id": request_id, "result": probe(scenario)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

