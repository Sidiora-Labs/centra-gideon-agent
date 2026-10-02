"""Strict evidence production from completed Hypermid gate executions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import shutil
import tempfile
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import ClassVar, Literal, Mapping

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

SOURCE_DIGEST_PREFIX = b"git-commit\0"
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_PRIVATE_PATH = re.compile(r"(?<![A-Za-z0-9._-])/(?:root|home|tmp)/[^\s\"']+")
_WINDOWS_PRIVATE_PATH = re.compile(r"(?i)(?<![A-Za-z0-9._-])[A-Z]:\\Users\\[^\s\"']+")
_SECRET = re.compile(
    r"(?i)(?:bearer\s+)[A-Za-z0-9._~+/=-]+|"
    r"(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+"
)

Gate = Literal[
    "capability_matrix",
    "authentication",
    "cache_bytes",
    "long_history",
    "raw_recovery",
    "effect_recovery",
    "migration",
    "deletion_export_backup_restore",
    "network_secrets_sandbox",
    "model_budget",
    "artifact_supply_chain",
    "live_provider",
    "client_compatibility",
]
Comparator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "digest_eq", "byte_eq"]
Scalar = int | float | str | bool


class EvidenceProductionError(ValueError):
    """Raised when actual gate facts cannot produce safe, strict evidence."""


@dataclass(frozen=True, slots=True)
class Measurement:
    name: str
    observed: Scalar
    comparator: Comparator
    threshold: Scalar
    unit: str | None = None


@dataclass(frozen=True, slots=True)
class Artifact:
    artifact_id: str
    path: Path
    media_type: str | None = None


@dataclass(frozen=True, slots=True)
class Environment:
    platform: str
    architecture: str
    daemon_version: str
    client_version: str

    @classmethod
    def current(cls, *, daemon_version: str, client_version: str) -> Environment:
        return cls(platform.platform(), platform.machine(), daemon_version, client_version)


class ObservationWriter:
    """Opt-in collector for facts measured inside a real qualification gate."""

    ENV_DIRECTORY = "HYPERMID_EVIDENCE_OBSERVATIONS_DIR"
    ENV_SOURCE_DIGEST = "HYPERMID_SOURCE_DIGEST"
    _instances: ClassVar[dict[tuple[str, str], ObservationWriter]] = {}
    _instances_lock: ClassVar[threading.RLock] = threading.RLock()

    def __init__(self, gate: Gate, directory: Path) -> None:
        if gate not in Gate.__args__:
            raise EvidenceProductionError(f"unsupported observation gate {gate!r}")
        self.gate = gate
        self.directory = directory
        self._measurements: dict[str, Measurement] = {}
        self._artifacts: dict[str, Artifact] = {}
        self._finished = False
        self._lock = threading.RLock()

    @classmethod
    def from_env(cls, gate: Gate) -> ObservationWriter | None:
        configured = os.environ.get(cls.ENV_DIRECTORY, "").strip()
        if not configured:
            return None
        directory = Path(configured).expanduser().resolve()
        key = (str(directory), str(gate))
        with cls._instances_lock:
            writer = cls._instances.get(key)
            if writer is None:
                writer = cls(gate, directory)
                cls._instances[key] = writer
            return writer

    def measure(
        self,
        name: str,
        observed: Scalar,
        comparator: Comparator,
        threshold: Scalar,
        unit: str | None = None,
    ) -> None:
        value = Measurement(name, observed, comparator, threshold, unit)
        _measurement(value)
        with self._lock:
            self._require_open()
            if name in self._measurements:
                raise EvidenceProductionError(
                    f"duplicate observation measurement {name!r}"
                )
            self._measurements[name] = value

    def artifact(
        self,
        artifact_id: str,
        path: str | Path,
        media_type: str | None = None,
    ) -> None:
        value = Artifact(artifact_id, _regular_file(Path(path), artifact_id), media_type)
        if not _ID.fullmatch(artifact_id):
            raise EvidenceProductionError("artifact identifier is not a Hypermid ID")
        if media_type is not None:
            if not isinstance(media_type, str) or len(media_type) > 128:
                raise EvidenceProductionError("artifact media type is invalid")
            _require_public_text(media_type, "artifact media type")
        with self._lock:
            self._require_open()
            if artifact_id in self._artifacts:
                raise EvidenceProductionError(
                    f"duplicate observation artifact {artifact_id!r}"
                )
            self._artifacts[artifact_id] = value

    def finish(self, *, source_digest: str | None = None) -> Path:
        digest = str(
            source_digest or os.environ.get(self.ENV_SOURCE_DIGEST, "")
        ).strip()
        if not _DIGEST.fullmatch(digest):
            raise EvidenceProductionError(
                "observation source digest must be a lowercase SHA-256 digest"
            )
        with self._lock:
            self._require_open()
            if not self._measurements:
                raise EvidenceProductionError("observation requires measured facts")
            if not self._artifacts:
                raise EvidenceProductionError("observation requires an artifact")
            self.directory.mkdir(mode=0o755, parents=True, exist_ok=True)
            output = self.directory / f"{self.gate}.observation.json"
            if output.exists() or output.is_symlink():
                raise EvidenceProductionError(
                    f"observation already exists for gate {self.gate}"
                )
            artifact_root = self.directory / "artifacts" / self.gate
            if artifact_root.exists() or artifact_root.is_symlink():
                raise EvidenceProductionError(
                    f"observation artifacts already exist for gate {self.gate}"
                )
            staging_parent = self.directory / ".staging"
            staging_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(prefix=f"{self.gate}-", dir=staging_parent)
            )
            records: list[dict[str, object]] = []
            try:
                for artifact in self._artifacts.values():
                    suffix = artifact.path.suffix if len(artifact.path.suffix) <= 16 else ""
                    destination = staging / f"{artifact.artifact_id}{suffix}"
                    shutil.copyfile(artifact.path, destination)
                    records.append(
                        {
                            "artifact_id": artifact.artifact_id,
                            "path": str(
                                Path("artifacts") / self.gate / destination.name
                            ),
                            "digest": _sha256(destination),
                            "size_bytes": destination.stat().st_size,
                            **(
                                {"media_type": artifact.media_type}
                                if artifact.media_type is not None
                                else {}
                            ),
                        }
                    )
                artifact_root.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
                os.replace(staging, artifact_root)
                document = {
                    "schema_version": 1,
                    "gate": self.gate,
                    "source_digest": digest,
                    "measurements": [
                        _measurement(value) for value in self._measurements.values()
                    ],
                    "artifacts": records,
                }
                _write_json_exclusive(output, document)
            except BaseException:
                if staging.exists():
                    shutil.rmtree(staging)
                raise
            self._finished = True
            return output

    def _require_open(self) -> None:
        if self._finished:
            raise EvidenceProductionError("observation writer is already finished")


@dataclass(frozen=True, slots=True)
class GateExecution:
    gate: Gate
    capability_id: str
    scope: Mapping[str, str]
    trace: Mapping[str, str]
    command: str
    exit_code: int
    started_at: datetime
    ended_at: datetime
    environment: Environment
    log_path: Path
    provider_live: bool = False
    explicit_opt_in: bool = False


def produce_observed_evidence(
    output_dir: str | Path,
    *,
    observation_path: str | Path,
    execution: GateExecution,
    source_revision: str,
    source_snapshot_digest: str,
) -> Path:
    observation = _read_observation(
        Path(observation_path),
        expected_gate=execution.gate,
        expected_source_digest=source_snapshot_digest,
    )
    measurements = [
        Measurement(
            name=str(item["name"]),
            observed=item["observed"],
            comparator=item["comparator"],
            threshold=item["threshold"],
            unit=item.get("unit"),
        )
        for item in observation["measurements"]
    ]
    artifacts = [
        Artifact(
            artifact_id=str(item["artifact_id"]),
            path=Path(item["resolved_path"]),
            media_type=item.get("media_type"),
        )
        for item in observation["artifacts"]
    ]
    return produce_evidence(
        output_dir,
        gate=execution.gate,
        capability_id=execution.capability_id,
        scope=execution.scope,
        trace=execution.trace,
        command=execution.command,
        exit_code=execution.exit_code,
        started_at=execution.started_at,
        ended_at=execution.ended_at,
        environment=execution.environment,
        measurements=measurements,
        log_path=execution.log_path,
        artifacts=artifacts,
        source_revision=source_revision,
        source_snapshot_digest=source_snapshot_digest,
        provider_live=execution.provider_live,
        explicit_opt_in=execution.explicit_opt_in,
    )


def source_digest(revision: str) -> str:
    if (
        not isinstance(revision, str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", revision)
    ):
        raise EvidenceProductionError("source revision must be lowercase hexadecimal")
    return hashlib.sha256(SOURCE_DIGEST_PREFIX + revision.encode("ascii")).hexdigest()


def produce_evidence(
    output_dir: str | Path,
    *,
    gate: Gate,
    capability_id: str,
    scope: Mapping[str, str],
    trace: Mapping[str, str],
    command: str,
    exit_code: int,
    started_at: datetime,
    ended_at: datetime,
    environment: Environment,
    measurements: tuple[Measurement, ...] | list[Measurement],
    log_path: str | Path,
    artifacts: tuple[Artifact, ...] | list[Artifact] = (),
    source_revision: str,
    source_snapshot_digest: str,
    path_kind: Literal["real", "mocked"] = "real",
    provider_live: bool = False,
    explicit_opt_in: bool = False,
    product_acceptance_claim: bool = True,
    evidence_id: str | None = None,
) -> Path:
    if not isinstance(exit_code, int) or isinstance(exit_code, bool) or exit_code < 0:
        raise EvidenceProductionError("command exit code must be a non-negative integer")
    expected_source = source_digest(source_revision)
    if source_snapshot_digest != expected_source:
        raise EvidenceProductionError("source snapshot digest does not match revision")
    if not _DIGEST.fullmatch(source_snapshot_digest):
        raise EvidenceProductionError("source snapshot digest is invalid")
    if not isinstance(command, str) or not command or len(command) > 4096:
        raise EvidenceProductionError("command is empty or exceeds the evidence contract")
    _require_public_text(command, "command")
    if not isinstance(environment, Environment):
        raise EvidenceProductionError("environment must be measured Environment facts")
    for label, value in (
        ("environment platform", environment.platform),
        ("environment architecture", environment.architecture),
        ("daemon version", environment.daemon_version),
        ("client version", environment.client_version),
    ):
        if not isinstance(value, str) or not value:
            raise EvidenceProductionError(f"{label} is empty")
        _require_public_text(value, label)
    start = _utc(started_at, "started_at")
    end = _utc(ended_at, "ended_at")
    if end < start:
        raise EvidenceProductionError("gate ended before it started")
    if not measurements:
        raise EvidenceProductionError("evidence requires measured truth")
    measured = [_measurement(item) for item in measurements]
    command_passed = exit_code == 0
    all_measurements_passed = all(item["passed"] for item in measured)
    passed = command_passed and all_measurements_passed
    measured.insert(
        0,
        {
            "name": "command-exit-code",
            "observed": exit_code,
            "comparator": "eq",
            "threshold": 0,
            "passed": command_passed,
        },
    )
    if gate == "live_provider":
        if path_kind != "real" or not provider_live or not explicit_opt_in:
            raise EvidenceProductionError(
                "live_provider requires real execution and explicit live opt-in"
            )
    elif provider_live:
        raise EvidenceProductionError(
            "provider_live evidence must use the live_provider gate"
        )
    claim = bool(product_acceptance_claim and passed and path_kind == "real")

    output = Path(output_dir)
    output.mkdir(mode=0o755, parents=True, exist_ok=True)
    artifact_dir = output / "artifacts" / gate
    artifact_dir.mkdir(mode=0o755, parents=True, exist_ok=True)
    evidence_path = output / f"{gate}.json"
    if evidence_path.exists():
        raise EvidenceProductionError("evidence record already exists")

    recorded_artifacts: list[dict[str, object]] = []
    log_source = _regular_file(Path(log_path), "command log")
    public_log = artifact_dir / f"{gate}-command.log"
    sanitized_log = _sanitize_log(
        log_source.read_text(encoding="utf-8", errors="replace")
    )
    _require_public_text(sanitized_log, "sanitized command log")
    public_log.write_text(sanitized_log, encoding="utf-8")
    recorded_artifacts.append(
        _artifact_record("command-log", public_log, evidence_path, "text/plain")
    )
    seen_ids = {"command-log"}
    for artifact in artifacts:
        if not isinstance(artifact, Artifact):
            raise EvidenceProductionError("artifacts must be Artifact values")
        if not _ID.fullmatch(artifact.artifact_id) or artifact.artifact_id in seen_ids:
            raise EvidenceProductionError("artifact identifiers must be unique Hypermid IDs")
        seen_ids.add(artifact.artifact_id)
        source = _regular_file(artifact.path, f"artifact {artifact.artifact_id}")
        _require_public_artifact(source, artifact.artifact_id)
        suffix = source.suffix if len(source.suffix) <= 16 else ""
        destination = artifact_dir / f"{artifact.artifact_id}{suffix}"
        if destination.exists():
            raise EvidenceProductionError("artifact destination already exists")
        shutil.copyfile(source, destination)
        recorded_artifacts.append(
            _artifact_record(
                artifact.artifact_id,
                destination,
                evidence_path,
                artifact.media_type,
            )
        )

    errors: list[dict[str, object]] = []
    if not command_passed:
        errors.append(
            {
                "code": "COMMAND_FAILED",
                "message": f"gate command exited with status {exit_code}; inspect command-log",
                "retryable": False,
                "effect_state": "unknown",
            }
        )
    if not all_measurements_passed:
        errors.append(
            {
                "code": "MEASUREMENT_FAILED",
                "message": "one or more measured gate conditions did not pass",
                "retryable": False,
                "effect_state": "unknown",
            }
        )

    identifier = evidence_id or (
        f"evidence-{gate}-{source_snapshot_digest[:12]}-"
        f"{int(start.timestamp() * 1_000_000)}"
    )
    record = {
        "schema_version": 1,
        "evidence_id": identifier,
        "capability_id": capability_id,
        "scope": dict(scope),
        "gate": gate,
        "source_digest": source_snapshot_digest,
        "command": command,
        "environment": {
            "platform": environment.platform,
            "architecture": environment.architecture,
            "daemon_version": environment.daemon_version,
            "client_version": environment.client_version,
        },
        "started_at": _timestamp(start),
        "ended_at": _timestamp(end),
        "execution": {
            "path_kind": path_kind,
            "provider_live": provider_live,
            "explicit_opt_in": explicit_opt_in,
            "product_acceptance_claim": claim,
        },
        "trace": dict(trace),
        "result": "passed" if passed else "failed",
        "measurements": measured,
        "errors": errors,
        "artifacts": recorded_artifacts,
    }
    _validate_schema(record, Path(__file__).resolve().parents[2])
    _atomic_json(evidence_path, record)
    return evidence_path


def _measurement(value: Measurement) -> dict[str, object]:
    if not isinstance(value, Measurement):
        raise EvidenceProductionError("measurements must be Measurement values")
    if not _ID.fullmatch(value.name):
        raise EvidenceProductionError("measurement name is not a Hypermid ID")
    for item in (value.observed, value.threshold):
        if not isinstance(item, (int, float, str, bool)):
            raise EvidenceProductionError("measurement values must be JSON scalars")
        if isinstance(item, float) and not math.isfinite(item):
            raise EvidenceProductionError("measurement values must be finite")
        if isinstance(item, str):
            _require_public_text(item, f"measurement {value.name}")
    try:
        if value.comparator in ("eq", "digest_eq", "byte_eq"):
            passed = value.observed == value.threshold
        elif value.comparator == "ne":
            passed = value.observed != value.threshold
        elif value.comparator == "lt":
            passed = value.observed < value.threshold
        elif value.comparator == "lte":
            passed = value.observed <= value.threshold
        elif value.comparator == "gt":
            passed = value.observed > value.threshold
        elif value.comparator == "gte":
            passed = value.observed >= value.threshold
        else:
            raise EvidenceProductionError("measurement comparator is unsupported")
    except TypeError as error:
        raise EvidenceProductionError("measurement values cannot be compared") from error
    result: dict[str, object] = {
        "name": value.name,
        "observed": value.observed,
        "comparator": value.comparator,
        "threshold": value.threshold,
        "passed": passed,
    }
    if value.unit is not None:
        if not isinstance(value.unit, str) or len(value.unit) > 64:
            raise EvidenceProductionError("measurement unit is invalid")
        _require_public_text(value.unit, f"measurement {value.name} unit")
        result["unit"] = value.unit
    return result


def _read_observation(
    path: Path,
    *,
    expected_gate: Gate,
    expected_source_digest: str,
) -> dict[str, object]:
    source = _regular_file(path, "observation")

    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise EvidenceProductionError(
                    f"observation contains duplicate key {key!r}"
                )
            result[key] = value
        return result

    try:
        document = json.loads(source.read_text(encoding="utf-8"), object_pairs_hook=unique)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise EvidenceProductionError("observation is not valid JSON") from error
    if not isinstance(document, dict) or set(document) != {
        "schema_version",
        "gate",
        "source_digest",
        "measurements",
        "artifacts",
    }:
        raise EvidenceProductionError("observation has an unsupported shape")
    if document["schema_version"] != 1:
        raise EvidenceProductionError("observation schema version is unsupported")
    if document["gate"] != expected_gate:
        raise EvidenceProductionError("observation gate does not match execution")
    if document["source_digest"] != expected_source_digest:
        raise EvidenceProductionError("observation source digest is stale")
    raw_measurements = document["measurements"]
    if not isinstance(raw_measurements, list) or not raw_measurements:
        raise EvidenceProductionError("observation has no measured facts")
    measured: list[dict[str, object]] = []
    names: set[str] = set()
    for raw in raw_measurements:
        if not isinstance(raw, dict):
            raise EvidenceProductionError("observation measurement is not an object")
        required = {"name", "observed", "comparator", "threshold", "passed"}
        if not required.issubset(raw) or set(raw).difference(required | {"unit"}):
            raise EvidenceProductionError("observation measurement has an unsupported shape")
        name = raw["name"]
        if not isinstance(name, str) or name in names:
            raise EvidenceProductionError("observation measurement names must be unique")
        names.add(name)
        value = Measurement(
            name,
            raw["observed"],
            raw["comparator"],
            raw["threshold"],
            raw.get("unit"),
        )
        normalized = _measurement(value)
        if raw != normalized:
            raise EvidenceProductionError("observation measurement result is inconsistent")
        measured.append(normalized)
    raw_artifacts = document["artifacts"]
    if not isinstance(raw_artifacts, list) or not raw_artifacts:
        raise EvidenceProductionError("observation has no artifacts")
    artifact_root = source.parent.resolve()
    artifacts: list[dict[str, object]] = []
    artifact_ids: set[str] = set()
    artifact_paths: set[str] = set()
    for raw in raw_artifacts:
        if not isinstance(raw, dict):
            raise EvidenceProductionError("observation artifact is not an object")
        required = {"artifact_id", "path", "digest", "size_bytes"}
        if not required.issubset(raw) or set(raw).difference(required | {"media_type"}):
            raise EvidenceProductionError("observation artifact has an unsupported shape")
        artifact_id = raw["artifact_id"]
        declared = raw["path"]
        if not isinstance(artifact_id, str) or not _ID.fullmatch(artifact_id):
            raise EvidenceProductionError("observation artifact identifier is invalid")
        if artifact_id in artifact_ids:
            raise EvidenceProductionError("observation artifact identifiers must be unique")
        if not isinstance(declared, str) or not declared or declared in artifact_paths:
            raise EvidenceProductionError("observation artifact paths must be unique")
        artifact_ids.add(artifact_id)
        artifact_paths.add(declared)
        relative = Path(declared)
        if relative.is_absolute():
            raise EvidenceProductionError("observation artifact path is absolute")
        candidate = _regular_file(source.parent / relative, artifact_id)
        try:
            candidate.relative_to(artifact_root)
        except ValueError as error:
            raise EvidenceProductionError("observation artifact escapes its directory") from error
        if raw["digest"] != _sha256(candidate):
            raise EvidenceProductionError("observation artifact digest changed")
        if raw["size_bytes"] != candidate.stat().st_size:
            raise EvidenceProductionError("observation artifact size changed")
        artifacts.append({**raw, "resolved_path": str(candidate)})
    return {
        "schema_version": 1,
        "gate": expected_gate,
        "source_digest": expected_source_digest,
        "measurements": measured,
        "artifacts": artifacts,
    }


def _regular_file(path: Path, label: str) -> Path:
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise EvidenceProductionError(f"{label} is unavailable") from error
    if path.is_symlink() or not stat_is_regular(info.st_mode):
        raise EvidenceProductionError(f"{label} must be a regular non-symlink file")
    return resolved


def stat_is_regular(mode: int) -> bool:
    import stat

    return stat.S_ISREG(mode)


def _require_public_text(value: str, label: str) -> None:
    if _PRIVATE_PATH.search(value) or _WINDOWS_PRIVATE_PATH.search(value):
        raise EvidenceProductionError(f"{label} contains a private absolute path")
    if _SECRET.search(value):
        raise EvidenceProductionError(f"{label} contains credential-shaped material")


def _sanitize_log(value: str) -> str:
    sanitized = _PRIVATE_PATH.sub("<private-path>", value)
    sanitized = _WINDOWS_PRIVATE_PATH.sub("<private-path>", sanitized)
    sanitized = _SECRET.sub("[REDACTED]", sanitized)
    return sanitized


def _require_public_artifact(path: Path, artifact_id: str) -> None:
    with path.open("rb") as stream:
        content = stream.read()
    if any(prefix in content for prefix in (b"/root/", b"/home/", b"/tmp/")) or re.search(
        br"[A-Za-z]:\\Users\\", content, re.I
    ):
        raise EvidenceProductionError(f"artifact {artifact_id} contains a private path")
    if re.search(
        br"(?i)(?:bearer\s+)[A-Za-z0-9._~+/=-]+|"
        br"(?:api[_-]?key|authorization|password|secret|token)\s*[:=]\s*[^\s,;]+",
        content,
    ):
        raise EvidenceProductionError(
            f"artifact {artifact_id} contains credential-shaped material"
        )


def _artifact_record(
    artifact_id: str,
    path: Path,
    evidence_path: Path,
    media_type: str | None,
) -> dict[str, object]:
    relative = path.relative_to(evidence_path.parent)
    result: dict[str, object] = {
        "artifact_id": artifact_id,
        "path": relative.as_posix(),
        "digest": _sha256(path),
        "size_bytes": path.stat().st_size,
    }
    if media_type is not None:
        if not isinstance(media_type, str) or len(media_type) > 128:
            raise EvidenceProductionError("artifact media type is invalid")
        result["media_type"] = media_type
    return result


def _sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _utc(value: datetime, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EvidenceProductionError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_schema(record: object, repo: Path) -> None:
    schema_dir = repo / "spec" / "hypermid" / "schemas"
    evidence_schema = json.loads((schema_dir / "evidence.schema.json").read_text())
    common_schema = json.loads((schema_dir / "common.schema.json").read_text())
    registry = Registry().with_resources(
        (
            (evidence_schema["$id"], Resource.from_contents(evidence_schema)),
            (common_schema["$id"], Resource.from_contents(common_schema)),
        )
    )
    validator = Draft202012Validator(
        evidence_schema,
        registry=registry,
        format_checker=FormatChecker(),
    )
    issues = sorted(validator.iter_errors(record), key=lambda issue: list(issue.path))
    if issues:
        detail = "; ".join(issue.message for issue in issues[:5])
        raise EvidenceProductionError(f"produced evidence violates schema: {detail}")


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_exclusive(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise EvidenceProductionError(f"output already exists: {path.name}") from error
    finally:
        temporary.unlink(missing_ok=True)


__all__ = [
    "Artifact",
    "Environment",
    "EvidenceProductionError",
    "GateExecution",
    "Measurement",
    "ObservationWriter",
    "produce_evidence",
    "produce_observed_evidence",
    "source_digest",
]
