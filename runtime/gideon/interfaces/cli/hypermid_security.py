"""Native CLI consumer for owner-bound encrypted recovery snapshots."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping
from pathlib import Path
import re
from typing import Any

from gideon.core.config import config_dir
from gideon.hypermid.adapter import HypermidAdapter
from gideon.hypermid.client import HypermidClient
from gideon.hypermid.credential_authority import attach_security_operations
from gideon.hypermid.foundation import Scope
from gideon.hypermid.handlers import HypermidHandlers, service_for
from gideon.hypermid.lifecycle import HypermidLifecycle, LocalEnrollment
from gideon.hypermid.models import JsonValue


_MAX_PLAN_BYTES = 8 * 1024 * 1024
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


def add_security_parser(commands: Any) -> None:
    security = commands.add_parser(
        "security", help="Create and restore encrypted recovery snapshots"
    )
    actions = security.add_subparsers(dest="security_command", required=True)
    actions.add_parser(
        "credentials", help="Show the configured owner-bound recovery credential"
    )

    plan = actions.add_parser("plan", help="Prepare a reviewable security plan")
    plan.add_argument("action", choices=("backup", "restore"))
    plan.add_argument("--destination")
    plan.add_argument("--export-id")
    plan.add_argument("--artifact-path")
    plan.add_argument("--source-digest")

    apply = actions.add_parser("apply", help="Apply an exact reviewed security plan")
    apply.add_argument("action", choices=("backup", "restore"))
    apply.add_argument("--plan", required=True, help="Reviewed plan JSON file")
    apply.add_argument("--plan-digest", required=True)
    apply.add_argument("--confirm-destructive", action="store_true")

    status = actions.add_parser("status", help="Read an authoritative security receipt")
    status.add_argument("job_id")
    recover = actions.add_parser(
        "recover", help="Recover a security operation with an uncertain outcome"
    )
    recover.add_argument("job_id")


def security_handlers(
    client: HypermidClient, connection_record: str | Path, scope: Scope
) -> HypermidHandlers:
    enrollment = LocalEnrollment.load(
        config_dir() / "hypermid" / "enrollment.json", scope=scope
    )
    owner = HypermidLifecycle(
        HypermidAdapter(client),
        object(),
        connection_record=connection_record,
        enrollment=enrollment,
    )
    if attach_security_operations(owner) is None:
        raise PermissionError(
            "Hypermid enrollment does not authorize encrypted recovery snapshots"
        )
    return service_for(owner)


async def run_security_command(
    args: argparse.Namespace,
    client: HypermidClient,
    connection_record: str | Path,
    scope: Scope,
) -> Mapping[str, Any]:
    handlers = security_handlers(client, connection_record, scope)
    command = args.security_command
    if command == "credentials":
        return await handlers.dispatch("security.credentials", {})
    if command == "plan":
        return await handlers.dispatch(
            f"security.{args.action}.plan", _plan_payload(args)
        )
    if command == "apply":
        plan = _plan_file(args.plan)
        expected_operation = f"security.{args.action}.apply"
        if plan.get("operation") != expected_operation:
            raise ValueError(
                f"reviewed plan operation must be {expected_operation}"
            )
        plan_id = _text(plan.get("plan_id"), "reviewed plan id")
        plan_digest = _digest(args.plan_digest, "reviewed plan digest")
        if plan.get("plan_digest") != plan_digest:
            raise ValueError("reviewed plan file does not match --plan-digest")
        return await handlers.dispatch(
            expected_operation,
            {
                "plan_id": plan_id,
                "plan_digest": plan_digest,
                "confirm_destructive": args.confirm_destructive,
            },
        )
    job_id = _text(args.job_id, "security job id")
    return await handlers.dispatch(f"security.{command}", {"job_id": job_id})


def _plan_payload(args: argparse.Namespace) -> dict[str, JsonValue]:
    if args.action == "backup":
        destination = _text(args.destination, "backup destination")
        if args.artifact_path or args.source_digest:
            raise ValueError("restore options cannot be used with a backup plan")
        payload: dict[str, JsonValue] = {"destination": destination}
        if args.export_id:
            payload["export_id"] = _text(args.export_id, "backup export id")
        return payload
    artifact_path = _text(args.artifact_path, "restore artifact path")
    source_digest = _digest(args.source_digest, "restore source digest")
    if args.destination or args.export_id:
        raise ValueError("backup options cannot be used with a restore plan")
    return {
        "artifact_path": artifact_path,
        "source_digest": source_digest,
    }


def _plan_file(value: str) -> dict[str, JsonValue]:
    path = Path(value).expanduser()
    if not path.is_file() or path.is_symlink():
        raise ValueError("reviewed plan must be a regular JSON file")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_PLAN_BYTES + 1)
    if len(raw) > _MAX_PLAN_BYTES:
        raise ValueError("reviewed plan exceeds the 8 MiB input limit")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("reviewed plan is not valid JSON") from error
    if not isinstance(parsed, dict):
        raise ValueError("reviewed plan must be a JSON object")
    return parsed


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError(f"{name} is required")
    return value.strip()


def _digest(value: object, name: str) -> str:
    text = _text(value, name)
    if not _DIGEST.fullmatch(text):
        raise ValueError(f"{name} must be 64 lowercase hexadecimal characters")
    return text


__all__ = ["add_security_parser", "run_security_command", "security_handlers"]
