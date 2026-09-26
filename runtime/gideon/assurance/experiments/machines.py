"""Operator local and SSH resource registry. Run with ``python -m ...machines``."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from gideon.core.atomic_write import atomic_write
from gideon.core.config import loader as config_loader


_SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _path() -> Path:
    return config_loader.config_dir() / "experiments" / "machines.json"


def list_machines() -> list[dict[str, Any]]:
    path = _path()
    if not path.exists():
        return []
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise ValueError("machine registry must be an array")
    ids: set[str] = set()
    for row in rows:
        validate(row)
        if row["id"] in ids:
            raise ValueError("duplicate machine ID")
        ids.add(row["id"])
    return rows


def validate(row: Any) -> None:
    if not isinstance(row, dict) or row.get("kind") not in ("local", "ssh"):
        raise ValueError("machine must have kind local or ssh")
    if not isinstance(row.get("id"), str) or not _SAFE.fullmatch(row["id"]):
        raise ValueError("machine ID must use letters, digits, dot, dash or underscore")
    slots = row.get("slots")
    if not isinstance(slots, int) or isinstance(slots, bool) or not 1 <= slots <= 128:
        raise ValueError("slots must be an integer from 1 to 128")
    if row["kind"] == "ssh":
        for key in ("host", "user"):
            if not isinstance(row.get(key), str) or not _SAFE.fullmatch(row[key]):
                raise ValueError(f"invalid {key}")
        for key in ("identity_file", "known_hosts_file"):
            raw = row.get(key)
            if not isinstance(raw, str) or not Path(raw).is_absolute():
                raise ValueError(f"{key} must be an absolute path")
        port = row.get("port", 22)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("port must be from 1 to 65535")


def add(row: dict[str, Any]) -> None:
    validate(row)
    rows = list_machines()
    if any(item["id"] == row["id"] for item in rows):
        raise ValueError("machine ID already exists")
    if row["kind"] == "ssh" and any(item.get("host") == row["host"] and item.get("user") == row["user"] and item.get("port", 22) == row.get("port", 22) for item in rows):
        raise ValueError("SSH destination already registered")
    rows.append(row)
    atomic_write(_path(), json.dumps(rows, indent=2, sort_keys=True), mode=0o600)


def remove(machine_id: str) -> None:
    rows = list_machines()
    remaining = [row for row in rows if row["id"] != machine_id]
    if len(remaining) == len(rows):
        raise KeyError(machine_id)
    atomic_write(_path(), json.dumps(remaining, indent=2, sort_keys=True), mode=0o600)


def probe(machine_id: str, *, timeout: int = 10) -> dict[str, Any]:
    row = next((item for item in list_machines() if item["id"] == machine_id), None)
    if row is None:
        raise KeyError(machine_id)
    if row["kind"] == "local":
        command = ["uname", "-s"]
    else:
        identity = Path(row["identity_file"])
        known_hosts = Path(row["known_hosts_file"])
        if not identity.is_file() or not known_hosts.is_file():
            raise ValueError("SSH identity or known_hosts file is missing")
        command = [
            "ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={known_hosts}",
            "-o", f"ConnectTimeout={min(timeout, 30)}", "-i", str(identity),
            "-p", str(row.get("port", 22)), f"{row['user']}@{row['host']}", "uname", "-s",
        ]
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout + 2, check=False)
    return {"id": machine_id, "reachable": result.returncode == 0, "system": result.stdout.strip()[:100] if result.returncode == 0 else "", "exit_code": result.returncode}


def main() -> None:
    parser = argparse.ArgumentParser(description="Gideon operator machine registry")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    add_cmd = sub.add_parser("add")
    add_cmd.add_argument("json_file", type=Path)
    remove_cmd = sub.add_parser("remove")
    remove_cmd.add_argument("id")
    probe_cmd = sub.add_parser("probe")
    probe_cmd.add_argument("id")
    args = parser.parse_args()
    if args.command == "list":
        print(json.dumps([{k: v for k, v in row.items() if k not in ("identity_file", "known_hosts_file")} for row in list_machines()], indent=2))
    elif args.command == "add":
        add(json.loads(args.json_file.read_text()))
        print("registered")
    elif args.command == "remove":
        remove(args.id)
        print("removed")
    else:
        print(json.dumps(probe(args.id), indent=2))


if __name__ == "__main__":
    main()
