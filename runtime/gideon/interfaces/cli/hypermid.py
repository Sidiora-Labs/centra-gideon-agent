"""Native Gideon CLI commands for the local Hypermid daemon."""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping

from gideon.core.config import config_dir
from gideon.hypermid.client import (
    HypermidClient,
    HypermidConnectionError,
    HypermidOutcomeUnknown,
    HypermidProtocolError,
    HypermidRemoteError,
)
from gideon.hypermid.operator_lifecycle import HypermidOperatorLifecycle, LifecyclePlan
from gideon.hypermid.models import Cursor, JsonValue, Scope
from gideon.hypermid.operations import (
    ActionPlan,
    ActionReceipt,
    HypermidOperations,
    OperatorContractError,
)
from gideon.interfaces.cli.hypermid_security import (
    add_security_parser,
    run_security_command,
)

EXIT_OK = 0
EXIT_INTERNAL = 1
EXIT_USAGE = 2
EXIT_UNAVAILABLE = 3
EXIT_REFUSED = 4
EXIT_OUTCOME_UNKNOWN = 5
EXIT_FAILED = 6
EXIT_CANCELLED = 7

_MAX_INPUT_BYTES = 8 * 1024 * 1024


def add_parser(subparsers: Any) -> None:
    parser = subparsers.add_parser(
        "hypermid", help="Inspect and operate the local Hypermid service"
    )
    parser.add_argument("--json", action="store_true", help="Emit canonical JSON")
    parser.add_argument(
        "--connection-record",
        default=os.environ.get("HYPERMID_CONNECTION_RECORD", ""),
        help="Path to the protected daemon connection descriptor",
    )
    parser.add_argument("--owner", required=True, help="Owner scope identifier")
    parser.add_argument("--project", required=True, help="Project scope identifier")
    parser.add_argument("--workspace", help="Optional workspace scope identifier")
    commands = parser.add_subparsers(dest="hypermid_command", required=True)
    add_security_parser(commands)

    commands.add_parser("status", help="Show authoritative daemon and adapter status")
    doctor = commands.add_parser("doctor", help="Run scoped Hypermid diagnostics")
    doctor.add_argument(
        "--refresh",
        action="store_true",
        help="Run fresh diagnostic checks instead of using the cached snapshot",
    )

    inspect = commands.add_parser("inspect", help="Inspect authoritative scoped state")
    inspect.add_argument("resource", choices=("sessions", "memory", "cache"))
    inspect.add_argument("--id", dest="resource_id")
    inspect.add_argument("--filters", help="JSON object or @FILE")
    inspect.add_argument("--after", help="Cursor as EPOCH:SEQUENCE")

    config = commands.add_parser("config", help="Read or change scoped configuration")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_get = config_sub.add_parser("get")
    config_get.add_argument("key", nargs="?")
    config_set = config_sub.add_parser("set")
    config_set.add_argument("key")
    config_set.add_argument("value", help="JSON value or @FILE")
    config_set.add_argument("--expected-digest", required=True)

    maintenance = commands.add_parser("maintenance", help="Plan and run maintenance")
    maintenance_sub = maintenance.add_subparsers(
        dest="maintenance_command", required=True
    )
    maintenance_plan = maintenance_sub.add_parser("plan")
    maintenance_plan.add_argument(
        "action",
        choices=(
            "integrity_check",
            "reconcile",
            "reindex",
            "compact",
            "cleanup_stale_cache",
            "rebuild_derivatives",
        ),
    )
    maintenance_plan.add_argument("--params", help="JSON object or @FILE")
    maintenance_apply = maintenance_sub.add_parser("apply")
    maintenance_apply.add_argument(
        "--plan", required=True, help="Reviewed plan JSON file"
    )
    maintenance_apply.add_argument("--plan-digest", required=True)
    maintenance_apply.add_argument("--confirm-destructive", action="store_true")
    maintenance_status = maintenance_sub.add_parser("status")
    maintenance_status.add_argument("job_id")
    maintenance_cancel = maintenance_sub.add_parser("cancel")
    maintenance_cancel.add_argument("job_id")

    lifecycle = commands.add_parser("lifecycle", help="Plan and run lifecycle changes")
    _add_lifecycle_subparsers(lifecycle)

    conditions = commands.add_parser("conditions", help="Evaluate a bounded condition")
    conditions.add_argument("expression", help="JSON object, @FILE, or - for stdin")

    export = commands.add_parser("export", help="Plan or apply a verified export")
    export_sub = export.add_subparsers(dest="export_command", required=True)
    export_plan = export_sub.add_parser("plan")
    export_plan.add_argument("destination")
    export_plan.add_argument("--params", help="JSON object or @FILE")
    export_apply = export_sub.add_parser("apply")
    export_apply.add_argument("--plan", required=True)
    export_apply.add_argument("--plan-digest", required=True)


def _add_lifecycle_subparsers(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="lifecycle_command", required=True)
    plan = commands.add_parser("plan")
    plan.add_argument(
        "action",
        choices=("install", "update", "uninstall", "migrate", "restore", "rollback"),
    )
    plan.add_argument("--target-version")
    plan.add_argument("--data", choices=("retain", "export", "purge"))
    plan.add_argument("--source")
    plan.add_argument("--destination")
    plan.add_argument("--resume-after", help="Cursor as EPOCH:SEQUENCE")
    plan.add_argument("--params", help="JSON object or @FILE")
    apply = commands.add_parser("apply")
    apply.add_argument(
        "action",
        choices=("install", "update", "uninstall", "migrate", "restore", "rollback"),
    )
    apply.add_argument("--plan", required=True, help="Reviewed plan JSON file")
    apply.add_argument("--plan-digest", required=True)
    apply.add_argument("--confirm-destructive", action="store_true")
    apply.add_argument("--confirm-purge", action="store_true")
    status = commands.add_parser("status")
    status.add_argument("job_id")
    recover = commands.add_parser("recover")
    recover.add_argument("job_id")
    resume = commands.add_parser("resume")
    resume.add_argument("job_id")
    resume.add_argument("--after", help="Cursor as EPOCH:SEQUENCE")
    rollback = commands.add_parser("rollback")
    rollback.add_argument(
        "--plan", required=True, help="Reviewed rollback plan JSON file"
    )
    rollback.add_argument("--plan-digest", required=True)
    rollback.add_argument("--confirm-destructive", action="store_true")


def hypermid_cmd(args: argparse.Namespace) -> int:
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        _emit_error(args, "cancelled", "operation cancelled before a terminal receipt")
        return EXIT_CANCELLED
    except HypermidOutcomeUnknown as exc:
        _emit_error(args, "outcome_unknown", str(exc))
        return EXIT_OUTCOME_UNKNOWN
    except (HypermidConnectionError, HypermidProtocolError, FileNotFoundError) as exc:
        _emit_error(args, "unavailable", str(exc))
        return EXIT_UNAVAILABLE
    except (HypermidRemoteError, OperatorContractError, PermissionError, ValueError) as exc:
        _emit_error(args, "refused", str(exc))
        return EXIT_REFUSED
    except OSError as exc:
        _emit_error(args, "failed", str(exc))
        return EXIT_FAILED
    except Exception as exc:
        _emit_error(args, "internal_error", str(exc))
        return EXIT_INTERNAL


async def _run(args: argparse.Namespace) -> int:
    scope = Scope(args.owner, args.project, args.workspace)
    connection_record = (
        Path(args.connection_record).expanduser()
        if args.connection_record
        else config_dir() / "hypermid" / "connection.json"
    )
    async with HypermidClient(connection_record, scope=scope) as client:
        operations = HypermidOperations(client)
        lifecycle = HypermidOperatorLifecycle(client)
        command = args.hypermid_command
        if command == "status":
            result: object = await operations.status()
        elif command == "doctor":
            result = await operations.diagnostics(refresh=args.refresh)
        elif command == "inspect":
            payload: dict[str, JsonValue] = {}
            if args.resource_id:
                payload["id"] = args.resource_id
            if args.filters:
                payload["filters"] = _json_object(args.filters, "filters")
            if args.after:
                payload["after"] = _parse_cursor(args.after).to_wire()
            result = await client.request(f"inspect.{args.resource}", payload)
        elif command == "config":
            result = await _config(client, args)
        elif command == "maintenance":
            result = await _maintenance(operations, args)
        elif command == "lifecycle":
            result = await _lifecycle(lifecycle, args)
        elif command == "conditions":
            result = await client.request(
                "conditions.evaluate",
                {"expression": _json_object(args.expression, "condition")},
            )
        elif command == "export":
            result = await _export(lifecycle, args)
        elif command == "security":
            result = await run_security_command(args, client, connection_record, scope)
        else:
            raise ValueError("unknown Hypermid command")
    _emit(args, result)
    return _result_exit(result)


async def _config(client: HypermidClient, args: argparse.Namespace) -> object:
    if args.config_command == "get":
        payload: dict[str, JsonValue] = {}
        if args.key:
            payload["key"] = args.key
        return await client.request("config.get", payload)
    value = _json_value(args.value, "configuration value")
    return await client.request(
        "config.set",
        {
            "key": args.key,
            "value": value,
            "expected_digest": args.expected_digest,
        },
        effect_kind="durable",
    )


async def _maintenance(
    operations: HypermidOperations, args: argparse.Namespace
) -> object:
    if args.maintenance_command == "plan":
        params = _json_object(args.params, "maintenance params") if args.params else {}
        return await operations.plan_maintenance(args.action, params=params)
    if args.maintenance_command == "apply":
        plan_value = _json_object(f"@{args.plan}", "maintenance plan")
        plan = ActionPlan.from_wire(plan_value, operations.client.scope)
        return await operations.apply_maintenance(
            plan,
            reviewed_digest=args.plan_digest,
            confirm_destructive=args.confirm_destructive,
        )
    if args.maintenance_command == "status":
        return await operations.maintenance_status(args.job_id)
    return await operations.cancel_maintenance(args.job_id)


async def _lifecycle(
    lifecycle: HypermidOperatorLifecycle, args: argparse.Namespace
) -> object:
    if args.lifecycle_command == "plan":
        params = _json_object(args.params, "lifecycle params") if args.params else {}
        return await lifecycle.plan(
            args.action,
            target_version=args.target_version,
            data_disposition=args.data,
            source=args.source,
            destination=args.destination,
            resume_after=(
                _parse_cursor(args.resume_after) if args.resume_after else None
            ),
            params=params,
        )
    if args.lifecycle_command == "apply":
        plan_value = _json_object(f"@{args.plan}", "lifecycle plan")
        plan = LifecyclePlan.from_wire(args.action, plan_value, lifecycle.client.scope)
        return await lifecycle.apply(
            plan,
            reviewed_digest=args.plan_digest,
            confirm_destructive=args.confirm_destructive,
            confirm_purge=args.confirm_purge,
        )
    if args.lifecycle_command == "status":
        return await lifecycle.status(args.job_id)
    if args.lifecycle_command == "recover":
        return await lifecycle.recover(args.job_id)
    if args.lifecycle_command == "resume":
        return await lifecycle.resume(
            args.job_id, after=_parse_cursor(args.after) if args.after else None
        )
    plan_value = _json_object(f"@{args.plan}", "rollback plan")
    plan = LifecyclePlan.from_wire("rollback", plan_value, lifecycle.client.scope)
    return await lifecycle.apply(
        plan,
        reviewed_digest=args.plan_digest,
        confirm_destructive=args.confirm_destructive,
    )


async def _export(lifecycle: HypermidOperatorLifecycle, args: argparse.Namespace) -> object:
    if args.export_command == "plan":
        params = _json_object(args.params, "export params") if args.params else {}
        return await lifecycle.plan(
            "export", destination=args.destination, params=params
        )
    plan_value = _json_object(f"@{args.plan}", "export plan")
    plan = LifecyclePlan.from_wire("export", plan_value, lifecycle.client.scope)
    return await lifecycle.apply(plan, reviewed_digest=args.plan_digest)


def _parse_cursor(value: str) -> Cursor:
    try:
        epoch, sequence = value.split(":", 1)
        return Cursor(int(epoch), int(sequence))
    except (ValueError, TypeError) as exc:
        raise ValueError("cursor must be EPOCH:SEQUENCE") from exc


def _json_object(value: str, name: str) -> dict[str, JsonValue]:
    parsed = _json_value(value, name)
    if not isinstance(parsed, dict):
        raise ValueError(f"{name} must be a JSON object")
    return parsed


def _json_value(value: str, name: str) -> JsonValue:
    if value == "-":
        if sys.stdin.isatty():
            raise ValueError("stdin input requires redirected non-interactive data")
        raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    elif value.startswith("@"):
        path = Path(value[1:])
        if not path.is_file():
            raise ValueError(f"{name} file does not exist")
        with path.open("rb") as stream:
            raw = stream.read(_MAX_INPUT_BYTES + 1)
    else:
        raw = value.encode("utf-8")
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError(f"{name} exceeds the 8 MiB input limit")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{name} is not valid JSON") from exc
    return parsed


def _wire(value: object) -> JsonValue:
    if isinstance(value, Scope):
        return value.to_wire()
    if isinstance(value, Cursor):
        return value.to_wire()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _wire(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if getattr(value, field.name) is not None
        }
    if isinstance(value, Mapping):
        return {str(key): _wire(item) for key, item in value.items()}
    if isinstance(value, (set, frozenset)):
        return [_wire(item) for item in sorted(value, key=str)]
    if isinstance(value, (tuple, list)):
        return [_wire(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _emit(args: argparse.Namespace, value: object) -> None:
    wire = _wire(value)
    if args.json:
        print(json.dumps(wire, sort_keys=True, separators=(",", ":")))
        return
    _print_human(wire)


def _print_human(value: JsonValue, prefix: str = "") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            label = f"{prefix}{key}"
            if isinstance(item, (Mapping, list)):
                print(f"{label}:")
                _print_human(item, f"{prefix}  ")
            else:
                print(f"{label}: {_human_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (Mapping, list)):
                print(f"{prefix}-")
                _print_human(item, f"{prefix}  ")
            else:
                print(f"{prefix}- {_human_scalar(item)}")
    else:
        print(f"{prefix}{_human_scalar(value)}")


def _human_scalar(value: JsonValue) -> str:
    if value is True:
        return "yes"
    if value is False:
        return "no"
    if value is None:
        return "unknown"
    return str(value)


def _emit_error(args: argparse.Namespace, state: str, message: str) -> None:
    if getattr(args, "json", False):
        print(
            json.dumps(
                {"ok": False, "state": state, "message": message},
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
    else:
        print(f"Hypermid {state}: {message}", file=sys.stderr)


def _result_exit(value: object) -> int:
    state: object = None
    if isinstance(value, ActionReceipt):
        state = value.state
    elif dataclasses.is_dataclass(value) and hasattr(value, "state"):
        state = getattr(value, "state")
    elif dataclasses.is_dataclass(value) and hasattr(value, "receipt"):
        receipt = getattr(value, "receipt")
        state = getattr(receipt, "state", None)
        if dataclasses.is_dataclass(receipt) and hasattr(receipt, "receipt"):
            state = getattr(getattr(receipt, "receipt"), "state", state)
    elif isinstance(value, Mapping):
        state = value.get("state")
    if state == "outcome_unknown":
        return EXIT_OUTCOME_UNKNOWN
    if state == "failed":
        return EXIT_FAILED
    if state == "cancelled":
        return EXIT_CANCELLED
    return EXIT_OK


__all__ = [
    "EXIT_CANCELLED",
    "EXIT_FAILED",
    "EXIT_INTERNAL",
    "EXIT_OK",
    "EXIT_OUTCOME_UNKNOWN",
    "EXIT_REFUSED",
    "EXIT_UNAVAILABLE",
    "EXIT_USAGE",
    "add_parser",
    "hypermid_cmd",
]
