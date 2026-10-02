from __future__ import annotations

import asyncio
import hashlib
import importlib.resources
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(os.environ["GIDEON_HOME"]).resolve()
SOCKET = ROOT / "hypermid.sock"
RECORD = ROOT / "connection.json"
MARKER = ROOT / "packaged-install-probe.json"
STORE = ROOT / "memory.sqlite3"


def _package_root() -> Path:
    resource = importlib.resources.files("gideon.hypermid")
    return Path(str(resource))


def _daemon(package_root: Path) -> Path:
    packaged = package_root / "bin" / (
        "hypermid-daemon.exe" if os.name == "nt" else "hypermid-daemon"
    )
    if packaged.is_file():
        return packaged
    resolved = shutil.which("hypermid-daemon")
    if resolved:
        return Path(resolved)
    raise AssertionError("packaged hypermid-daemon is absent")


def _listening_tcp() -> set[str]:
    listeners: set[str] = set()
    for source in (Path("/proc/net/tcp"), Path("/proc/net/tcp6")):
        if not source.is_file():
            continue
        for line in source.read_text(encoding="ascii").splitlines()[1:]:
            fields = line.split()
            if len(fields) > 3 and fields[3] == "0A":
                listeners.add(fields[1])
    return listeners


def _assert_assets(package_root: Path) -> tuple[Path, list[Path]]:
    schema_root = package_root / "schemas"
    schemas = sorted(schema_root.glob("*.json"))
    assert schemas, "packaged Hypermid schemas are absent"
    assert {"config.schema.json", "common.schema.json"} <= {
        path.name for path in schemas
    }
    console = package_root.parent / "static" / "dist" / "index.html"
    assert console.is_file(), "packaged console index is absent"
    return console, schemas


async def _daemon_health() -> dict[str, object]:
    from gideon.hypermid.client import HypermidClient
    from gideon.hypermid.models import PROTOCOL, Scope

    client = HypermidClient(
        RECORD,
        scope=Scope("package-owner", "package-project", "package-workspace"),
    )
    try:
        session = await client.connect()
        description = await client.describe_typed()
        echoed = await client.passthrough({"probe": "packaged-install"})
        assert description.protocol == PROTOCOL
        assert description.daemon_instance_id == client.daemon_instance_id
        assert description.health.get("status") == "healthy"
        checks = description.health.get("checks")
        assert isinstance(checks, dict)
        assert checks.get("process", {}).get("value") == "live"
        assert checks.get("transport", {}).get("value") == "tls13_mutual_auth"
        assert echoed == {"probe": "packaged-install"}
        return {
            "daemon_instance_id": description.daemon_instance_id,
            "protocol": description.protocol,
            "session_id": session.session_id,
        }
    finally:
        await client.close()


def _wait_for_record(process: subprocess.Popen[bytes]) -> None:
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stderr = process.stderr.read().decode("utf-8", "replace") if process.stderr else ""
            raise AssertionError(f"hypermid-daemon exited before health check: {stderr}")
        if RECORD.is_file() and SOCKET.exists():
            return
        time.sleep(0.05)
    raise AssertionError("hypermid-daemon did not publish its local connection record")


def first_run() -> dict[str, object]:
    assert hasattr(os, "geteuid") and os.geteuid() != 0, "container probe must be unprivileged"
    assert ROOT == Path("/data")
    ROOT.mkdir(parents=True, exist_ok=True)
    package_root = _package_root()
    console, schemas = _assert_assets(package_root)
    daemon = _daemon(package_root)
    daemon_bytes = daemon.read_bytes()
    assert daemon_bytes.startswith(b"\x7fELF"), "packaged daemon is not a Linux executable"
    assert os.access(daemon, os.X_OK)

    from gideon.core.config.loader import AppConfig
    from gideon.hypermid.config import ContextConfig

    assert ContextConfig().mode.value == "off"
    assert AppConfig().hypermid.mode.value == "off"
    assert not RECORD.exists(), "installation started Hypermid before explicit activation"

    listeners_before = _listening_tcp()
    process = subprocess.Popen(
        [
            os.fspath(daemon),
            "--socket",
            os.fspath(SOCKET),
            "--connection-record",
            os.fspath(RECORD),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        _wait_for_record(process)
        assert stat.S_IMODE(RECORD.stat().st_mode) == 0o600
        record = json.loads(RECORD.read_text(encoding="utf-8"))
        assert record["endpoint"] == os.fspath(SOCKET)
        assert _listening_tcp() == listeners_before
        health = asyncio.run(_daemon_health())
        cli = subprocess.run(
            [
                "gideon",
                "hypermid",
                "--json",
                "--connection-record",
                os.fspath(RECORD),
                "--owner",
                "package-owner",
                "--project",
                "package-project",
                "--workspace",
                "package-workspace",
                "status",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=20,
        )
        assert cli.returncode == 0, cli.stderr
        cli_status = json.loads(cli.stdout)
        assert cli_status.get("status") == "healthy"
        assert cli_status.get("daemon_instance_id") == health["daemon_instance_id"]
        assert cli_status.get("daemon", {}).get("transport", {}).get("value") == (
            "tls13_mutual_auth"
        )
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    marker = {
        "daemon_sha256": hashlib.sha256(daemon_bytes).hexdigest(),
        "protocol": health["protocol"],
        "schema_count": len(schemas),
        "console_sha256": hashlib.sha256(console.read_bytes()).hexdigest(),
        "store_bytes": STORE.stat().st_size,
        "uid": os.geteuid(),
    }
    MARKER.write_text(json.dumps(marker, sort_keys=True), encoding="utf-8")
    MARKER.chmod(0o600)
    return marker


def second_run() -> dict[str, object]:
    assert os.geteuid() != 0
    package_root = _package_root()
    _assert_assets(package_root)
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    daemon = _daemon(package_root)
    assert hashlib.sha256(daemon.read_bytes()).hexdigest() == marker["daemon_sha256"]
    assert STORE.is_file()
    assert STORE.stat().st_size == marker["store_bytes"]
    assert stat.S_IMODE(MARKER.stat().st_mode) == 0o600
    return marker


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"first", "second"}:
        raise SystemExit("usage: container_probe.py first|second")
    result = first_run() if sys.argv[1] == "first" else second_run()
    print(json.dumps({"phase": sys.argv[1], "result": result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
