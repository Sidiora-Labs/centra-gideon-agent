#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shlex
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

try:
    from checks.hypermid.evidence import (
        Environment,
        EvidenceProductionError,
        GateExecution,
        produce_observed_evidence,
    )
except ModuleNotFoundError:
    from evidence import (  # type: ignore[no-redef]
        Environment,
        EvidenceProductionError,
        GateExecution,
        produce_observed_evidence,
    )


GATE_EXECUTION_MAP = {
    "capability_matrix": {
        "command": "python -m pytest -q checks/hypermid/test_security_capabilities.py checks/hypermid/test_knowledge_authority_api.py",
        "measurements": frozenset(
            {"unauthorized-state-changes", "revoked-commit-changes"}
        ),
        "artifacts": frozenset({"command-log", "authorization-matrix"}),
    },
    "authentication": {
        "command": "python -m pytest -q checks/hypermid/test_auth_protocol.py checks/hypermid/test_memory_transport.py",
        "measurements": frozenset(
            {
                "plaintext-application-bytes",
                "invalid-sessions-accepted",
                "compatible-resume-passed",
            }
        ),
        "artifacts": frozenset({"command-log", "transport-auth-matrix"}),
    },
    "cache_bytes": {
        "command": "python -m pytest -q checks/hypermid/test_long_history.py checks/hypermid/test_cache_stability.py",
        "measurements": frozenset(
            {
                "cache-stable-turns",
                "stable-prefix-changed-bytes",
                "diagnostic-byte-ranges-retained",
            }
        ),
        "artifacts": frozenset({"command-log", "stable-prefix-byte-ranges"}),
    },
    "long_history": {
        "command": "python -m pytest -q checks/hypermid/test_long_history.py checks/hypermid/test_cache_stability.py",
        "measurements": frozenset(
            {
                "history-turns",
                "history-tool-results",
                "history-durable-memories",
                "expanded-compressed-spans",
                "cursor-monotonic",
            }
        ),
        "artifacts": frozenset({"command-log", "long-history-manifest"}),
    },
    "raw_recovery": {
        "command": "bash -lc 'cargo test -p hypermid-daemon --test read_only_recovery && python -m pytest -q checks/hypermid/test_memory_recovery.py'",
        "measurements": frozenset(
            {
                "raw-digest-mismatches",
                "orphaned-committed-records",
                "future-state-read-only",
            }
        ),
        "artifacts": frozenset({"command-log", "raw-recovery-manifest"}),
    },
    "effect_recovery": {
        "command": "python -m pytest -q checks/hypermid/test_effect_crash_recovery.py",
        "measurements": frozenset(
            {
                "effect-kill-boundaries",
                "duplicated-committed-effects",
                "unknown-effects-labeled-failed",
            }
        ),
        "artifacts": frozenset({"command-log", "effect-boundary-ledger"}),
    },
    "migration": {
        "command": "python checks/hypermid/verify_wave_07.py",
        "measurements": frozenset(
            {
                "migration-fixtures",
                "migration-digest-mismatches",
                "migration-interruption-boundaries",
                "future-store-mutations",
            }
        ),
        "artifacts": frozenset({"command-log", "migration-matrix"}),
    },
    "deletion_export_backup_restore": {
        "command": "python -m pytest -q checks/hypermid/test_memory_lifecycle_recovery.py checks/hypermid/test_security_operations.py",
        "measurements": frozenset(
            {
                "derived-copies-after-purge",
                "cross-owner-export-entries",
                "altered-backup-chunks-accepted",
                "restore-manifest-mismatches",
            }
        ),
        "artifacts": frozenset({"command-log", "lifecycle-integrity-report"}),
    },
    "network_secrets_sandbox": {
        "command": "python -m pytest -q checks/hypermid/test_egress_secrets.py checks/hypermid/test_sandbox_effects.py",
        "measurements": frozenset(
            {
                "unauthorized-connections",
                "unauthorized-file-accesses",
                "surviving-sandbox-children",
                "secret-canary-occurrences",
            }
        ),
        "artifacts": frozenset({"command-log", "network-secret-matrix"}),
    },
    "model_budget": {
        "command": "python -m pytest -q checks/hypermid/test_budget_provenance.py checks/hypermid/test_usage_accounting.py",
        "measurements": frozenset(
            {
                "admitted-calls-above-limit",
                "usage-rollup-mismatches",
                "unknown-charge-reservation-retained",
                "paused-reason-visible",
            }
        ),
        "artifacts": frozenset({"command-log", "budget-ledger-report"}),
    },
    "artifact_supply_chain": {
        "command": "bash -lc 'cargo test -p hypermid-artifacts && python -m pytest -q checks/hypermid/test_artifact_supply_chain.py'",
        "measurements": frozenset(
            {
                "executions-before-verification",
                "files-outside-staging",
                "failed-update-prior-version-losses",
                "unapproved-capability-expansions",
            }
        ),
        "artifacts": frozenset({"command-log", "artifact-verification-matrix"}),
    },
    "client_compatibility": {
        "command": "python -m pytest -q checks/hypermid/test_client_contracts.py",
        "measurements": frozenset(
            {
                "qualified-sdk-languages",
                "wrong-epoch-acceptances",
                "invalid-version-acceptances",
                "outcome-unknown-preserved",
            }
        ),
        "artifacts": frozenset({"command-log", "sdk-compatibility-matrix"}),
    },
    "live_provider": {
        "command": "python checks/hypermid/verify_wave_04.py",
        "measurements": frozenset(
            {
                "measured_input_tokens",
                "measured_output_tokens",
                "measured_cache_read_tokens",
                "measured_cache_creation_tokens",
                "cost_status",
                "price_source",
                "financial_reservation_unresolved",
                "provider_usage_reconciled",
                "redaction_passed",
                "approved_credentials",
            }
        ),
        "artifacts": frozenset({"command-log", "provider-usage-report"}),
    },
}
REQUIRED_GATES = tuple(
    gate for gate in GATE_EXECUTION_MAP if gate != "live_provider"
)
OPTIONAL_GATE = "live_provider"
SOURCE_DIGEST_PREFIX = b"git-commit\0"


class DuplicateKey(ValueError):
    pass


def declared_execution_map() -> dict[str, dict[str, object]]:
    return {
        gate: {
            "command": requirement["command"],
            "measurements": sorted(requirement["measurements"]),
            "artifacts": sorted(requirement["artifacts"]),
            "mandatory": gate in REQUIRED_GATES,
        }
        for gate, requirement in GATE_EXECUTION_MAP.items()
    }


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateKey(f"duplicate object key {key!r}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON number {value}")


def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(
            handle,
            object_pairs_hook=_unique_object,
            parse_constant=_reject_constant,
        )


def _run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(detail or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _checkout_source_digest(repo: Path) -> str:
    top = Path(_run_git(repo, "rev-parse", "--show-toplevel")).resolve()
    if top != repo.resolve():
        raise RuntimeError(f"expected repository root {repo}, found {top}")
    dirty = _run_git(repo, "status", "--porcelain", "--untracked-files=no")
    if dirty:
        raise RuntimeError("tracked checkout is not clean")
    revision = _run_git(repo, "rev-parse", "HEAD")
    return hashlib.sha256(SOURCE_DIGEST_PREFIX + revision.encode("ascii")).hexdigest()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no UTC offset")
    return parsed.astimezone(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _display_path(path: Path, base: Path) -> str:
    try:
        return str(path.relative_to(base))
    except ValueError:
        return str(path)


def _compare_measurement(measurement: dict[str, Any]) -> str | None:
    comparator = measurement.get("comparator")
    if comparator is None:
        return None
    if "threshold" not in measurement:
        return "sets comparator without threshold"
    observed = measurement["observed"]
    threshold = measurement["threshold"]
    try:
        if comparator in ("eq", "digest_eq", "byte_eq"):
            matches = observed == threshold
        elif comparator == "ne":
            matches = observed != threshold
        elif comparator == "lt":
            matches = observed < threshold
        elif comparator == "lte":
            matches = observed <= threshold
        elif comparator == "gt":
            matches = observed > threshold
        elif comparator == "gte":
            matches = observed >= threshold
        else:
            return f"uses unsupported comparator {comparator!r}"
    except TypeError:
        return f"cannot compare {observed!r} and {threshold!r} with {comparator}"
    if not matches:
        return f"does not satisfy {observed!r} {comparator} {threshold!r}"
    return None


def _validate_artifact(
    artifact: dict[str, Any], evidence_path: Path, evidence_dir: Path
) -> list[str]:
    errors: list[str] = []
    declared = Path(artifact["path"])
    label = artifact["artifact_id"]
    if declared.is_absolute():
        return [f"artifact {label} has an absolute path"]
    candidate = evidence_path.parent / declared
    try:
        resolved = candidate.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as exc:
        return [f"artifact {label} is unavailable: {exc}"]
    try:
        resolved.relative_to(evidence_dir.resolve())
    except ValueError:
        return [f"artifact {label} escapes the evidence directory"]
    if candidate.is_symlink() or not resolved.is_file():
        return [f"artifact {label} is not a regular non-symlink file"]
    actual_size = resolved.stat().st_size
    if actual_size != artifact["size_bytes"]:
        errors.append(
            f"artifact {label} size is {actual_size}, expected {artifact['size_bytes']}"
        )
    actual_digest = _sha256(resolved)
    if actual_digest != artifact["digest"]:
        errors.append(
            f"artifact {label} digest is {actual_digest}, expected {artifact['digest']}"
        )
    return errors


def _validate_live_provider(record: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    by_name = {measurement["name"]: measurement for measurement in record["measurements"]}
    status = by_name.get("cost_status", {}).get("observed")
    cost = by_name.get("actual_cost_nanodollars")
    if status == "provider_reported":
        if (
            cost is None
            or cost.get("unit") != "nanodollars"
            or cost.get("comparator") != "lte"
            or not isinstance(cost.get("observed"), (int, float))
            or isinstance(cost.get("observed"), bool)
            or not isinstance(cost.get("threshold"), (int, float))
            or isinstance(cost.get("threshold"), bool)
        ):
            errors.append(
                "provider-reported live cost requires numeric "
                "actual_cost_nanodollars with unit nanodollars and an lte ceiling"
            )
        if by_name.get("price_source", {}).get("observed") != "provider_reported":
            errors.append("provider-reported live cost requires price_source=provider_reported")
    elif status == "unpriced":
        if cost is not None:
            errors.append("unpriced live usage must not claim actual_cost_nanodollars")
        if by_name.get("price_source", {}).get("observed") != "unknown":
            errors.append("unpriced live usage requires price_source=unknown")
        if by_name.get("financial_reservation_unresolved", {}).get("observed") is not True:
            errors.append(
                "unpriced live usage requires financial_reservation_unresolved=true"
            )
    else:
        errors.append(
            "live_provider requires cost_status=provider_reported or cost_status=unpriced"
        )
    for name in ("measured_input_tokens", "measured_output_tokens"):
        observed = by_name.get(name, {}).get("observed")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed <= 0:
            errors.append(f"live_provider requires positive integer {name}")
    for name in ("measured_cache_read_tokens", "measured_cache_creation_tokens"):
        observed = by_name.get(name, {}).get("observed")
        if isinstance(observed, bool) or not isinstance(observed, int) or observed < 0:
            errors.append(f"live_provider requires non-negative integer {name}")
    for name in (
        "provider_usage_reconciled",
        "redaction_passed",
        "approved_credentials",
    ):
        measurement = by_name.get(name)
        if measurement is None or measurement.get("observed") is not True:
            errors.append(f"live_provider requires {name}=true")
    return errors


def _validator(repo: Path) -> Draft202012Validator:
    schema_dir = repo / "spec" / "hypermid" / "schemas"
    evidence_schema = _load_json(schema_dir / "evidence.schema.json")
    common_schema = _load_json(schema_dir / "common.schema.json")
    registry = Registry().with_resources(
        (
            (evidence_schema["$id"], Resource.from_contents(evidence_schema)),
            (common_schema["$id"], Resource.from_contents(common_schema)),
        )
    )
    return Draft202012Validator(
        evidence_schema,
        registry=registry,
        format_checker=FormatChecker(),
    )


def _schema_errors(
    validator: Draft202012Validator, record: Any
) -> list[str]:
    errors: list[str] = []
    for issue in sorted(validator.iter_errors(record), key=lambda item: list(item.path)):
        location = ".".join(str(part) for part in issue.absolute_path) or "$"
        errors.append(f"schema {location}: {issue.message}")
    return errors


def validate_evidence(
    *,
    repo: Path,
    evidence_dir: Path,
    expected_source_digest: str | None,
    allow_live_provider: bool,
) -> tuple[dict[str, Any], list[str]]:
    errors: list[str] = []
    records: dict[str, tuple[Path, dict[str, Any]]] = {}
    evidence_ids: set[str] = set()
    validator = _validator(repo)

    if not evidence_dir.is_dir():
        errors.append(f"evidence directory does not exist: {evidence_dir}")
        paths: list[Path] = []
    else:
        paths = sorted(evidence_dir.glob("*.json"))
        if not paths:
            errors.append(f"evidence directory contains no JSON records: {evidence_dir}")

    for path in paths:
        label = _display_path(path, evidence_dir)
        if path.is_symlink() or not path.is_file():
            errors.append(f"{label}: evidence record is not a regular non-symlink file")
            continue
        try:
            record = _load_json(path)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{label}: invalid JSON: {exc}")
            continue
        issues = _schema_errors(validator, record)
        if issues:
            errors.extend(f"{label}: {issue}" for issue in issues)
            continue

        gate = record["gate"]
        if gate == "release_acceptance":
            errors.append(f"{label}: release_acceptance cannot certify itself")
            continue
        if gate in records:
            errors.append(
                f"{label}: duplicate gate {gate!r}; first record is "
                f"{_display_path(records[gate][0], evidence_dir)}"
            )
            continue
        evidence_id = record["evidence_id"]
        if evidence_id in evidence_ids:
            errors.append(f"{label}: duplicate evidence_id {evidence_id!r}")
            continue
        evidence_ids.add(evidence_id)
        records[gate] = (path, record)

        requirement = GATE_EXECUTION_MAP.get(gate)
        if requirement is None:
            errors.append(f"{label}: gate {gate!r} has no release execution mapping")
        else:
            if record["command"] != requirement["command"]:
                errors.append(
                    f"{label}: command does not match the declared {gate} execution"
                )
            measured_names = {
                measurement["name"] for measurement in record["measurements"]
            }
            missing_measurements = sorted(
                requirement["measurements"] - measured_names
            )
            for name in missing_measurements:
                errors.append(f"{label}: missing required measurement: {name}")
            recorded_artifacts = {
                artifact["artifact_id"] for artifact in record["artifacts"]
            }
            missing_artifacts = sorted(
                requirement["artifacts"] - recorded_artifacts
            )
            for name in missing_artifacts:
                errors.append(f"{label}: missing required artifact: {name}")

        if record["source_digest"] != expected_source_digest:
            errors.append(
                f"{label}: stale source_digest {record['source_digest']}; "
                f"expected {expected_source_digest or 'a clean-checkout digest'}"
            )
        execution = record["execution"]
        if execution["path_kind"] != "real":
            errors.append(f"{label}: mocked execution cannot establish acceptance")
        if not execution["product_acceptance_claim"]:
            errors.append(f"{label}: product_acceptance_claim is false")
        if record["result"] != "passed":
            errors.append(f"{label}: result is {record['result']}, not passed")
        if record["errors"]:
            errors.append(f"{label}: passed evidence contains errors")
        if not record["measurements"]:
            errors.append(f"{label}: evidence contains no measurements")
        for measurement in record["measurements"]:
            name = measurement["name"]
            observed = measurement["observed"]
            if isinstance(observed, float) and not math.isfinite(observed):
                errors.append(f"{label}: measurement {name} is not finite")
            if not measurement["passed"]:
                errors.append(f"{label}: measurement {name} did not pass")
            comparison_error = _compare_measurement(measurement)
            if comparison_error:
                errors.append(f"{label}: measurement {name} {comparison_error}")
        if not record["artifacts"]:
            errors.append(f"{label}: evidence contains no artifacts")
        artifact_ids: set[str] = set()
        artifact_paths: set[str] = set()
        for artifact in record["artifacts"]:
            artifact_id = artifact["artifact_id"]
            artifact_path = artifact["path"]
            if artifact_id in artifact_ids:
                errors.append(f"{label}: duplicate artifact_id {artifact_id!r}")
            if artifact_path in artifact_paths:
                errors.append(f"{label}: duplicate artifact path {artifact_path!r}")
            artifact_ids.add(artifact_id)
            artifact_paths.add(artifact_path)
            errors.extend(
                f"{label}: {issue}"
                for issue in _validate_artifact(artifact, path, evidence_dir)
            )
        try:
            started_at = _parse_time(record["started_at"])
            ended_at = _parse_time(record["ended_at"])
            if started_at > ended_at:
                errors.append(f"{label}: started_at is after ended_at")
            if ended_at > datetime.now(timezone.utc) + timedelta(minutes=5):
                errors.append(f"{label}: ended_at exceeds the clock-skew allowance")
        except ValueError as exc:
            errors.append(f"{label}: invalid evidence time: {exc}")

        if gate == OPTIONAL_GATE:
            if not allow_live_provider:
                errors.append(
                    f"{label}: live_provider evidence requires --allow-live-provider"
                )
            errors.extend(f"{label}: {issue}" for issue in _validate_live_provider(record))
        elif execution["provider_live"]:
            errors.append(
                f"{label}: provider_live evidence must use the separate live_provider gate"
            )

    missing = [gate for gate in REQUIRED_GATES if gate not in records]
    for gate in missing:
        errors.append(f"missing mandatory gate: {gate}")
    if allow_live_provider and OPTIONAL_GATE not in records:
        errors.append("--allow-live-provider requires a live_provider evidence record")

    summary = {
        "status": "failed" if errors else "passed",
        "source_digest": expected_source_digest,
        "required_gates": list(REQUIRED_GATES),
        "validated_gates": sorted(records),
        "live_provider_requested": allow_live_provider,
        "live_provider_present": OPTIONAL_GATE in records,
        "execution_map": declared_execution_map(),
        "errors": errors,
    }
    return summary, errors


def _qualification_environment(collection_dir: Path) -> dict[str, str]:
    python_executable = Path(sys.executable).absolute()
    if not python_executable.is_file() or not os.access(python_executable, os.X_OK):
        raise RuntimeError("active Python interpreter is not executable")
    cargo_executable = shutil.which("cargo")
    if cargo_executable is None:
        cargo_candidate = Path.home() / ".cargo" / "bin" / "cargo"
        if cargo_candidate.is_file() and os.access(cargo_candidate, os.X_OK):
            cargo_executable = str(cargo_candidate.resolve())
    if cargo_executable is None:
        raise RuntimeError("cargo executable is unavailable for declared qualification gates")
    tool_directories = tuple(
        dict.fromkeys(
            (
                str(python_executable.parent),
                str(Path(cargo_executable).resolve().parent),
            )
        )
    )
    inherited_path = os.environ.get("PATH", "")
    process_path = os.pathsep.join(
        (*tool_directories, inherited_path) if inherited_path else tool_directories
    )
    bash_environment = collection_dir / "qualification-tools.bash"
    with bash_environment.open("x", encoding="utf-8") as handle:
        handle.write(f"export PATH={shlex.quote(process_path)}\n")
        handle.flush()
        os.fsync(handle.fileno())
    bash_environment.chmod(0o600)
    process_environment = os.environ.copy()
    process_environment.update(
        {
            "BASH_ENV": str(bash_environment),
            "PATH": process_path,
        }
    )
    return process_environment


def _daemon_binding(environment: dict[str, str]) -> dict[str, str]:
    configured = environment.get("HYPERMID_DAEMON_BINARY", "").strip()
    if not configured:
        raise RuntimeError("collection requires HYPERMID_DAEMON_BINARY")
    immutable = Path(configured).expanduser().resolve()
    if (
        not immutable.is_file()
        or immutable.is_symlink()
        or not os.access(immutable, os.X_OK)
    ):
        raise RuntimeError("immutable daemon is not a regular executable file")
    immutable_digest = _sha256(immutable)
    return {
        "immutable_path": str(immutable),
        "immutable_digest": immutable_digest,
    }


def _daemon_digests(binding: dict[str, str]) -> dict[str, str]:
    return {"immutable": _sha256(Path(binding["immutable_path"]))}


def collect_evidence(
    *,
    repo: Path,
    evidence_dir: Path,
    collection_dir: Path,
    expected_source_digest: str,
    environment: Environment,
    allow_live_provider: bool,
) -> dict[str, Any]:
    """Run every unique declared gate command once and produce strict evidence."""
    actual_source_digest = _checkout_source_digest(repo)
    if actual_source_digest != expected_source_digest:
        raise RuntimeError(
            "published checkout digest does not match the requested evidence source"
        )
    source_revision = _run_git(repo, "rev-parse", "HEAD")
    if evidence_dir.exists() or evidence_dir.is_symlink():
        raise RuntimeError("evidence output already exists")
    if collection_dir.exists() or collection_dir.is_symlink():
        raise RuntimeError("collection work directory already exists")
    observations_dir = collection_dir / "observations"
    logs_dir = collection_dir / "logs"
    staging_dir = collection_dir / "evidence-staging"
    observations_dir.mkdir(mode=0o755, parents=True)
    logs_dir.mkdir(mode=0o755)
    staging_dir.mkdir(mode=0o755)

    gates = list(REQUIRED_GATES)
    if allow_live_provider:
        gates.append(OPTIONAL_GATE)
    command_groups: dict[str, list[str]] = {}
    for gate in gates:
        command_groups.setdefault(GATE_EXECUTION_MAP[gate]["command"], []).append(gate)

    executions: list[dict[str, Any]] = []
    collection_errors: list[str] = []
    process_environment = _qualification_environment(collection_dir)
    process_environment.update(
        {
            "HYPERMID_EVIDENCE_OBSERVATIONS_DIR": str(observations_dir),
            "HYPERMID_SOURCE_DIGEST": expected_source_digest,
            "HYPERMID_SOURCE_REVISION": source_revision,
        }
    )
    daemon_binding = _daemon_binding(process_environment)
    daemon_expected_digest = daemon_binding["immutable_digest"]
    for index, (command, command_gates) in enumerate(command_groups.items(), start=1):
        if _checkout_source_digest(repo) != expected_source_digest:
            raise RuntimeError("checkout changed before a qualification gate")
        daemon_before = _daemon_digests(daemon_binding)
        if set(daemon_before.values()) != {daemon_expected_digest}:
            raise RuntimeError("daemon artifact changed before a qualification gate")
        command_id = hashlib.sha256(command.encode("utf-8")).hexdigest()[:16]
        log_path = logs_dir / f"{index:02d}-{command_id}.log"
        started_at = datetime.now(timezone.utc)
        with log_path.open("x", encoding="utf-8") as log:
            completed = subprocess.run(
                ["bash", "--noprofile", "--norc", "-c", command],
                cwd=repo,
                env=process_environment,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
            )
            log.flush()
            os.fsync(log.fileno())
        ended_at = datetime.now(timezone.utc)
        exit_code = (
            completed.returncode
            if completed.returncode >= 0
            else 128 + abs(completed.returncode)
        )
        if _checkout_source_digest(repo) != expected_source_digest:
            raise RuntimeError("checkout changed during a qualification gate")
        daemon_after = _daemon_digests(daemon_binding)
        daemon_changed = set(daemon_after.values()) != {daemon_expected_digest}
        if daemon_changed:
            for gate in command_gates:
                executions.append(
                    {
                        "gate": gate,
                        "command_id": command_id,
                        "exit_code": exit_code,
                        "evidence": None,
                        "source_before": expected_source_digest,
                        "source_after": expected_source_digest,
                        "daemon_before": daemon_before,
                        "daemon_after": daemon_after,
                        "error": "immutable_daemon_changed",
                    }
                )
            collection_errors.append(
                f"{', '.join(command_gates)}: immutable daemon changed during the command"
            )
            break
        missing = [
            gate
            for gate in command_gates
            if not (observations_dir / f"{gate}.observation.json").is_file()
        ]
        for gate in command_gates:
            observation_path = observations_dir / f"{gate}.observation.json"
            if gate in missing:
                executions.append(
                    {
                        "gate": gate,
                        "command_id": command_id,
                        "exit_code": exit_code,
                        "evidence": None,
                        "source_before": expected_source_digest,
                        "source_after": expected_source_digest,
                        "daemon_before": daemon_before,
                        "daemon_after": daemon_after,
                        "error": "observation_missing",
                    }
                )
                collection_errors.append(
                    f"{gate}: command emitted no observation document"
                )
                continue
            execution = GateExecution(
                gate=gate,
                capability_id=f"hypermid-release-{gate}",
                scope={"owner_id": "oss-release", "project_id": "hypermid"},
                trace={
                    "trace_id": f"sec08-{gate}",
                    "request_id": f"gate-{index}-{gate}",
                },
                command=command,
                exit_code=exit_code,
                started_at=started_at,
                ended_at=ended_at,
                environment=environment,
                log_path=log_path,
                provider_live=gate == OPTIONAL_GATE,
                explicit_opt_in=gate == OPTIONAL_GATE,
            )
            try:
                produced = produce_observed_evidence(
                    staging_dir,
                    observation_path=observation_path,
                    execution=execution,
                    source_revision=source_revision,
                    source_snapshot_digest=expected_source_digest,
                )
            except EvidenceProductionError as error:
                executions.append(
                    {
                        "gate": gate,
                        "command_id": command_id,
                        "exit_code": exit_code,
                        "evidence": None,
                        "source_before": expected_source_digest,
                        "source_after": expected_source_digest,
                        "daemon_before": daemon_before,
                        "daemon_after": daemon_after,
                        "error": str(error),
                    }
                )
                collection_errors.append(f"{gate}: {error}")
                continue
            executions.append(
                {
                    "gate": gate,
                    "command_id": command_id,
                    "exit_code": exit_code,
                    "evidence": produced.name,
                    "source_before": expected_source_digest,
                    "source_after": expected_source_digest,
                    "daemon_before": daemon_before,
                    "daemon_after": daemon_after,
                    "error": None,
                }
            )
        if exit_code != 0:
            collection_errors.append(
                f"{', '.join(command_gates)}: command exited with {exit_code}"
            )

    report, errors = validate_evidence(
        repo=repo,
        evidence_dir=staging_dir,
        expected_source_digest=expected_source_digest,
        allow_live_provider=allow_live_provider,
    )
    os.replace(staging_dir, evidence_dir)
    all_errors = [*collection_errors, *errors]
    return {
        "status": "failed" if all_errors else "passed",
        "source_digest": expected_source_digest,
        "unique_commands": len(command_groups),
        "gate_records": sum(item["evidence"] is not None for item in executions),
        "expected_gate_records": len(gates),
        "executions": executions,
        "validation": report,
        "collection_errors": collection_errors,
        "errors": all_errors,
    }


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect or validate source-bound Hypermid security acceptance evidence."
    )
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument(
        "--collect",
        action="store_true",
        help="Run each unique declared command once and produce evidence before validation.",
    )
    parser.add_argument(
        "--collection-dir",
        type=Path,
        help="New private work directory for raw logs and observations during --collect.",
    )
    parser.add_argument(
        "--daemon-version",
        help="Measured daemon version or digest recorded by --collect.",
    )
    parser.add_argument(
        "--client-version",
        help="Measured client version recorded by --collect.",
    )
    parser.add_argument(
        "--source-digest",
        help="Expected 64-character lowercase revision digest. Defaults to the clean checkout digest.",
    )
    parser.add_argument(
        "--allow-live-provider",
        action="store_true",
        help="Require and admit separately reported live-provider evidence.",
    )
    parser.add_argument("--report", type=Path, help="Write a validation summary atomically.")
    arguments = parser.parse_args()
    if arguments.collect:
        missing = [
            flag
            for flag, value in (
                ("--collection-dir", arguments.collection_dir),
                ("--daemon-version", arguments.daemon_version),
                ("--client-version", arguments.client_version),
            )
            if not value
        ]
        if missing:
            parser.error("--collect requires " + ", ".join(missing))
    return arguments


def main() -> int:
    args = _arguments()
    repo = Path(__file__).resolve().parents[2]
    source_errors: list[str] = []
    evidence_dir = args.evidence_dir.resolve()
    if args.report is not None:
        report_path = args.report.resolve()
        if report_path == evidence_dir or evidence_dir in report_path.parents:
            source_errors.append("--report must be outside --evidence-dir")
    expected_source_digest = args.source_digest
    if expected_source_digest is not None:
        if len(expected_source_digest) != 64 or any(
            character not in "0123456789abcdef" for character in expected_source_digest
        ):
            source_errors.append("--source-digest must be 64 lowercase hexadecimal characters")
            expected_source_digest = None
    else:
        try:
            expected_source_digest = _checkout_source_digest(repo)
        except RuntimeError as exc:
            source_errors.append(f"cannot bind evidence to checkout: {exc}")

    if args.collect:
        if source_errors or expected_source_digest is None:
            for error in source_errors:
                print(f"- {error}", file=sys.stderr)
            return 1
        try:
            report = collect_evidence(
                repo=repo,
                evidence_dir=evidence_dir,
                collection_dir=args.collection_dir.resolve(),
                expected_source_digest=expected_source_digest,
                environment=Environment.current(
                    daemon_version=args.daemon_version,
                    client_version=args.client_version,
                ),
                allow_live_provider=args.allow_live_provider,
            )
            if args.report is not None:
                _write_report(args.report, report)
        except (EvidenceProductionError, OSError, RuntimeError) as exc:
            print(f"security evidence collection failed: {exc}", file=sys.stderr)
            return 1
        if report["status"] != "passed":
            print(
                "Hypermid security evidence collection: FAILED "
                f"({report['gate_records']}/{report['expected_gate_records']} records preserved, "
                f"source {expected_source_digest})",
                file=sys.stderr,
            )
            for error in report["errors"]:
                print(f"- {error}", file=sys.stderr)
            return 1
        print(
            "Hypermid security evidence collected: PASSED "
            f"({report['gate_records']} gates, {report['unique_commands']} unique commands, "
            f"source {expected_source_digest})"
        )
        return 0

    try:
        report, errors = validate_evidence(
            repo=repo,
            evidence_dir=evidence_dir,
            expected_source_digest=expected_source_digest,
            allow_live_provider=args.allow_live_provider,
        )
    except Exception as exc:
        print(f"security acceptance validation could not run: {exc}", file=sys.stderr)
        return 1
    errors[:0] = source_errors
    report["errors"] = errors
    report["status"] = "failed" if errors else "passed"
    if args.report is not None:
        try:
            _write_report(args.report, report)
        except OSError as exc:
            errors.append(f"cannot write report: {exc}")
            report["status"] = "failed"
    if errors:
        print("Hypermid security acceptance: FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print(
        "Hypermid security acceptance: PASSED "
        f"({len(REQUIRED_GATES)} mandatory gates, source {expected_source_digest})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
