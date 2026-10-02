#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
import time
import tomllib
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
FLEET = ROOT.parent
if os.fspath(ROOT) not in sys.path:
    sys.path.insert(0, os.fspath(ROOT))

from checks.hypermid.packaging.source_closure import (
    SourceClosureError,
    capture_source_closure,
    freeze_source_tree,
)

PACKAGE_HELPER = ROOT / "checks/hypermid/packaging/clean_install.py"
SECURITY_RUNNER = ROOT / "checks/hypermid/verify_security_acceptance.py"
SDK_GATE = ROOT / "checks/hypermid/test_client_contracts.py"
NATIVE_GATE = ROOT / "checks/hypermid/test_surface_native_journey.py"
DESKTOP_GATE = ROOT / "apps/desktop/test/hypermidLifecycle.test.js"
SOURCE_DIGEST_PREFIX = b"git-commit\0"
DEFAULT_TARGET = FLEET / "build/target"
DEFAULT_EVIDENCE = ROOT / ".artifacts/hypermid/security"
DEFAULT_ARTIFACTS = FLEET / "artifacts/wave-08"
HEX = frozenset("0123456789abcdef")


class ReleaseFailure(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    command: tuple[str, ...]
    log: Path
    started_at: str
    ended_at: str
    elapsed_ms: int


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            json.dump(value, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _require_file(path: Path, label: str, *, executable: bool = False) -> Path:
    resolved = path.resolve()
    if path.is_symlink() or not resolved.is_file():
        raise ReleaseFailure(f"{label} is unavailable: {path}")
    if executable and not os.access(resolved, os.X_OK):
        raise ReleaseFailure(f"{label} is not executable: {path}")
    return resolved


def _source_digest(explicit: str | None) -> tuple[str, str]:
    top = Path(_git("rev-parse", "--show-toplevel")).resolve()
    if top != ROOT:
        raise ReleaseFailure(f"expected repository root {ROOT}, found {top}")
    revision = _git("rev-parse", "HEAD")
    declared_revision = os.environ.get("HYPERMID_SOURCE_REVISION")
    if declared_revision is not None and declared_revision != revision:
        raise ReleaseFailure("declared source revision does not match the published checkout")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise ReleaseFailure(
            "tracked checkout is not clean; release acceptance requires a published source revision"
        )
    observed = hashlib.sha256(SOURCE_DIGEST_PREFIX + revision.encode("ascii")).hexdigest()
    if explicit is not None:
        if len(explicit) != 64 or any(character not in HEX for character in explicit):
            raise ReleaseFailure("source digest must be 64 lowercase hexadecimal characters")
        if explicit != observed:
            raise ReleaseFailure("source digest does not match the published revision")
    return observed, revision


def _assert_source(
    *, revision: str, source_digest: str, closure_digest: str
) -> None:
    if _git("rev-parse", "HEAD") != revision:
        raise ReleaseFailure("published source revision changed during release acceptance")
    if _git("status", "--porcelain", "--untracked-files=no"):
        raise ReleaseFailure("tracked source changed during release acceptance")
    expected = hashlib.sha256(SOURCE_DIGEST_PREFIX + revision.encode("ascii")).hexdigest()
    if source_digest != expected:
        raise ReleaseFailure("published source digest changed during release acceptance")
    try:
        observed = capture_source_closure(ROOT)
    except SourceClosureError as error:
        raise ReleaseFailure(str(error)) from error
    if observed.get("source_digest") != closure_digest:
        raise ReleaseFailure("release source closure changed during gate execution")


def _assert_published_entries(closure: dict[str, Any]) -> None:
    tracked = {
        value
        for value in _git("ls-files", "-z").split("\0")
        if value
    }
    unexpected = sorted(
        str(entry.get("path"))
        for entry in closure.get("entries", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("path"), str)
        and entry["path"] not in tracked
        and not entry["path"].startswith("apps/console/dist/")
    )
    if unexpected:
        raise ReleaseFailure(
            "source closure contains unpublished files: " + ", ".join(unexpected[:5])
        )


def _git(*arguments: str, required: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", os.fspath(ROOT), *arguments],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if completed.returncode != 0:
        if not required:
            return ""
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise ReleaseFailure(detail or f"git {' '.join(arguments)} failed")
    return completed.stdout.strip()


def _log_process(
    name: str,
    command: list[str],
    log: Path,
    *,
    environment: dict[str, str],
    timeout: int,
) -> tuple[GateResult, str]:
    started_at = _utc_now()
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout.decode(errors="replace") if isinstance(error.stdout, bytes) else (error.stdout or "")
        stderr = error.stderr.decode(errors="replace") if isinstance(error.stderr, bytes) else (error.stderr or "")
        log.write_text(
            "COMMAND " + json.dumps(command) + "\n" + stdout + stderr + f"\nTIMEOUT {timeout}\n",
            encoding="utf-8",
        )
        raise ReleaseFailure(f"{name} timed out after {timeout}s; see {log}") from error
    ended_at = _utc_now()
    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.write_text(
        "COMMAND "
        + json.dumps(command)
        + "\nSTDOUT\n"
        + completed.stdout
        + "\nSTDERR\n"
        + completed.stderr
        + f"\nEXIT {completed.returncode}\n",
        encoding="utf-8",
    )
    if completed.returncode != 0:
        raise ReleaseFailure(
            f"{name} failed with exit {completed.returncode}; see {log}"
        )
    return (
        GateResult(
            name=name,
            command=tuple(command),
            log=log,
            started_at=started_at,
            ended_at=ended_at,
            elapsed_ms=elapsed_ms,
        ),
        completed.stdout,
    )


def _package_result(stdout: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if len(lines) != 1:
        raise ReleaseFailure("packaging harness did not return exactly one JSON result")
    try:
        result = json.loads(lines[0])
    except json.JSONDecodeError as error:
        raise ReleaseFailure("packaging harness returned invalid JSON") from error
    expected = {
        "cli_status",
        "configured_root_persistence",
        "console",
        "container_user",
        "daemon_health",
        "default_mode",
        "python_adapter",
        "transport",
        "wheel",
    }
    if not isinstance(result, dict) or set(result) != expected:
        raise ReleaseFailure("packaging harness returned an incomplete result")
    wheel = result["wheel"]
    if not isinstance(wheel, dict) or set(wheel) != {
        "daemon_path",
        "entry_count",
        "platform_wheel",
    }:
        raise ReleaseFailure("packaging harness returned incomplete wheel evidence")
    if (
        result["cli_status"] != "passed"
        or result["configured_root_persistence"] != "passed"
        or result["console"] != "present"
        or result["container_user"] not in {"gideon", "10001", "10001:10001"}
        or result["daemon_health"] != "passed"
        or result["default_mode"] != "off"
        or result["python_adapter"] != "passed"
        or result["transport"] != "authenticated_unix"
        or wheel["daemon_path"] != "gideon/hypermid/bin/hypermid-daemon"
        or type(wheel["entry_count"]) is not int
        or wheel["entry_count"] <= 0
        or wheel["platform_wheel"] is not True
    ):
        raise ReleaseFailure("packaging harness did not satisfy the OSS install contract")
    return result


def _package_artifacts(
    artifact_dir: Path,
    package: dict[str, Any],
    closure: dict[str, Any],
) -> tuple[list[tuple[Path, str, str]], Path]:
    closure_path = _require_file(artifact_dir / "source-closure.json", "packaging source closure")
    wheel_manifest_path = _require_file(
        artifact_dir / "wheel-manifest.json", "wheel manifest"
    )
    image_manifest_path = _require_file(
        artifact_dir / "image-manifest.json", "image manifest"
    )
    try:
        packaged_closure = json.loads(closure_path.read_text(encoding="utf-8"))
        wheel_manifest = json.loads(wheel_manifest_path.read_text(encoding="utf-8"))
        image_manifest = json.loads(image_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ReleaseFailure("packaging artifact manifest is invalid JSON") from error
    if packaged_closure != closure:
        raise ReleaseFailure("packaging used a different source closure")
    expected_wheel_fields = {
        "schema_version",
        "source_digest",
        "artifact",
        "sha256",
        "size_bytes",
        "contents",
    }
    if not isinstance(wheel_manifest, dict) or set(wheel_manifest) != expected_wheel_fields:
        raise ReleaseFailure("wheel manifest fields are invalid")
    if (
        wheel_manifest["schema_version"] != 1
        or wheel_manifest["source_digest"] != closure["source_digest"]
        or wheel_manifest["contents"] != package["wheel"]
        or not isinstance(wheel_manifest["artifact"], str)
        or Path(wheel_manifest["artifact"]).name != wheel_manifest["artifact"]
    ):
        raise ReleaseFailure("wheel manifest is not bound to the qualified package")
    wheel = _require_file(artifact_dir / wheel_manifest["artifact"], "preserved wheel")
    if (
        wheel_manifest["sha256"] != _sha256(wheel)
        or wheel_manifest["size_bytes"] != wheel.stat().st_size
    ):
        raise ReleaseFailure("preserved wheel does not match its manifest")
    expected_image_fields = {
        "schema_version",
        "source_digest",
        "image_tag",
        "image_id",
        "repo_digests",
        "config_user",
    }
    if not isinstance(image_manifest, dict) or set(image_manifest) != expected_image_fields:
        raise ReleaseFailure("image manifest fields are invalid")
    if (
        image_manifest["schema_version"] != 1
        or image_manifest["source_digest"] != closure["source_digest"]
        or not isinstance(image_manifest["image_id"], str)
        or len(image_manifest["image_id"]) != 71
        or not image_manifest["image_id"].startswith("sha256:")
        or any(character not in HEX for character in image_manifest["image_id"][7:])
        or not isinstance(image_manifest["repo_digests"], list)
        or image_manifest["config_user"] not in {"gideon", "10001", "10001:10001"}
    ):
        raise ReleaseFailure("image manifest is not bound to the qualified package")
    return (
        [
            (closure_path, "packaging-source-closure", "application/json"),
            (wheel_manifest_path, "wheel-manifest", "application/json"),
            (image_manifest_path, "image-manifest", "application/json"),
            (wheel, "packaged-wheel", "application/zip"),
        ],
        wheel,
    )


def _extract_packaged_daemon(
    wheel: Path, package: dict[str, Any], destination: Path
) -> Path:
    member = package["wheel"]["daemon_path"]
    try:
        with zipfile.ZipFile(wheel) as archive:
            payload = archive.read(member)
    except (KeyError, zipfile.BadZipFile) as error:
        raise ReleaseFailure("preserved wheel daemon is unavailable") from error
    if not payload.startswith(b"\x7fELF"):
        raise ReleaseFailure("preserved wheel daemon is not a Linux executable")
    daemon = destination / "hypermid-daemon"
    daemon.write_bytes(payload)
    daemon.chmod(0o755)
    return _require_file(daemon, "preserved wheel daemon", executable=True)


def _daemon_path(environment: dict[str, str]) -> Path:
    candidates = (
        environment.get("GIDEON_PREBUILT_HYPERMID_DAEMON"),
        environment.get("HYPERMID_DAEMON_BINARY"),
        os.fspath(Path(environment["CARGO_TARGET_DIR"]) / "debug/hypermid-daemon"),
    )
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if path.is_file() and os.access(path, os.X_OK):
                return path.resolve()
    raise ReleaseFailure("SDK gate did not leave an executable current-source daemon")


def _artifact(path: Path, run_dir: Path, identifier: str, media_type: str) -> dict[str, Any]:
    resolved = _require_file(path, identifier)
    try:
        relative = resolved.relative_to(run_dir.resolve())
    except ValueError as error:
        raise ReleaseFailure(f"release artifact escapes its run directory: {resolved}") from error
    return {
        "artifact_id": identifier,
        "path": os.fspath(relative),
        "digest": _sha256(resolved),
        "size_bytes": resolved.stat().st_size,
        "media_type": media_type,
    }


def _client_version() -> str:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        document = tomllib.load(handle)
    version = document.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise ReleaseFailure("pyproject.toml has no project version")
    return version


def run_release_acceptance(
    *,
    artifact_root: Path,
    evidence_dir: Path,
    source_digest: str | None,
    allow_live_provider: bool,
) -> dict[str, Any]:
    source, revision = _source_digest(source_digest)
    _require_file(PACKAGE_HELPER, "clean-install harness")
    _require_file(SECURITY_RUNNER, "security acceptance runner")
    _require_file(SDK_GATE, "SDK gate")
    _require_file(NATIVE_GATE, "native surface gate")
    _require_file(DESKTOP_GATE, "desktop lifecycle gate")
    evidence_dir = evidence_dir.resolve()
    if not evidence_dir.is_dir():
        raise ReleaseFailure(f"security evidence directory is unavailable: {evidence_dir}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + f"-{os.getpid()}"
    run_dir = artifact_root.resolve() / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    if run_dir == evidence_dir or evidence_dir in run_dir.parents or run_dir in evidence_dir.parents:
        raise ReleaseFailure("release artifacts and mandatory security evidence must be separate")

    environment = os.environ.copy()
    environment["PATH"] = f"/root/.cargo/bin:{environment.get('PATH', '')}"
    environment["PYTHONPATH"] = "runtime"
    environment["CARGO_BUILD_JOBS"] = "2"
    environment.setdefault("CARGO_TARGET_DIR", os.fspath(DEFAULT_TARGET))
    started_at = _utc_now()
    gates: list[GateResult] = []
    console_gate, _ = _log_process(
        "published console asset build",
        ["npm", "exec", "--workspace=apps/console", "--", "vite", "build"],
        run_dir / "console-build.log",
        environment=environment,
        timeout=600,
    )
    gates.append(console_gate)

    frozen_source = run_dir / "source"
    try:
        closure = freeze_source_tree(ROOT, frozen_source)
    except SourceClosureError as error:
        raise ReleaseFailure(str(error)) from error
    closure_digest = closure.get("source_digest")
    if not isinstance(closure_digest, str):
        raise ReleaseFailure("source closure did not produce a digest")
    _assert_published_entries(closure)
    bound_closure = {
        **closure,
        "published_revision": revision,
        "published_source_digest": source,
    }
    bound_closure_path = run_dir / "published-source-closure.json"
    _write_json(bound_closure_path, bound_closure)
    _assert_source(
        revision=revision,
        source_digest=source,
        closure_digest=closure_digest,
    )
    environment["HYPERMID_WAVE08_SOURCE_DIGEST"] = source
    environment["HYPERMID_WAVE08_CLOSURE_DIGEST"] = closure_digest
    environment["HYPERMID_WAVE08_RUN_DIR"] = os.fspath(run_dir)

    def checkpoint() -> None:
        _assert_source(
            revision=revision,
            source_digest=source,
            closure_digest=closure_digest,
        )

    package_detail = run_dir / "packaged-install-detail.log"
    package_artifact_dir = run_dir / "packaged-install-artifacts"
    package_gate, package_stdout = _log_process(
        "clean installed-wheel and single-container journey",
        [
            sys.executable,
            os.fspath(PACKAGE_HELPER),
            "--log",
            os.fspath(package_detail),
            "--source-root",
            os.fspath(frozen_source),
            "--artifact-dir",
            os.fspath(package_artifact_dir),
            "--source-digest",
            closure_digest,
        ],
        run_dir / "packaged-install.log",
        environment=environment,
        timeout=3_900,
    )
    gates.append(package_gate)
    checkpoint()
    package = _package_result(package_stdout)
    package_artifact_files, preserved_wheel = _package_artifacts(
        package_artifact_dir, package, closure
    )
    daemon = _extract_packaged_daemon(preserved_wheel, package, run_dir)
    environment["HYPERMID_DAEMON_BINARY"] = os.fspath(daemon)
    environment["HYPERMID_TEST_PYTHON"] = sys.executable
    package_path = run_dir / "packaged-install.json"
    _write_json(package_path, package)

    if not environment.get("HYPERMID_TEST_POSTGRES_DSN") or not environment.get(
        "HYPERMID_TEST_POSTGRES_DATABASE"
    ):
        raise ReleaseFailure(
            "HYPERMID_TEST_POSTGRES_DSN and HYPERMID_TEST_POSTGRES_DATABASE are required "
            "for declared PostgreSQL backend acceptance"
        )
    checkpoint()
    postgres_gate, _ = _log_process(
        "live PostgreSQL lease and fencing journey",
        [
            "cargo",
            "test",
            "-p",
            "hypermid-store-postgres",
            "tests::live_session_lock_reclaims_and_fences_an_old_epoch",
            "--",
            "--exact",
        ],
        run_dir / "postgres.log",
        environment=environment,
        timeout=600,
    )
    gates.append(postgres_gate)
    checkpoint()

    sdk_gate, _ = _log_process(
        "real daemon multi-language SDK journey",
        [sys.executable, "-m", "pytest", "-q", os.fspath(SDK_GATE.relative_to(ROOT))],
        run_dir / "sdk.log",
        environment=environment,
        timeout=1_200,
    )
    gates.append(sdk_gate)
    checkpoint()
    if _daemon_path(environment) != daemon:
        raise ReleaseFailure("SDK gate did not use the preserved packaged daemon")

    checkpoint()
    native_gate, _ = _log_process(
        "native Gideon CLI lifecycle journey",
        [sys.executable, "-m", "pytest", "-q", os.fspath(NATIVE_GATE.relative_to(ROOT))],
        run_dir / "native-cli.log",
        environment=environment,
        timeout=300,
    )
    gates.append(native_gate)
    checkpoint()
    desktop_gate, _ = _log_process(
        "packaged desktop daemon lifecycle journey",
        ["node", "--test", os.fspath(DESKTOP_GATE.relative_to(ROOT))],
        run_dir / "native-desktop.log",
        environment=environment,
        timeout=300,
    )
    gates.append(desktop_gate)
    checkpoint()

    security_report = run_dir / "security-validation.json"
    security_command = [
        sys.executable,
        os.fspath(SECURITY_RUNNER),
        "--evidence-dir",
        os.fspath(evidence_dir),
        "--source-digest",
        source,
        "--report",
        os.fspath(security_report),
    ]
    if allow_live_provider:
        security_command.append("--allow-live-provider")
    checkpoint()
    security_gate, _ = _log_process(
        "same-source security evidence validation",
        security_command,
        run_dir / "security-validation.log",
        environment=environment,
        timeout=120,
    )
    gates.append(security_gate)
    checkpoint()
    security = json.loads(security_report.read_text(encoding="utf-8"))
    if security.get("status") != "passed" or security.get("errors"):
        raise ReleaseFailure("security runner returned a non-passing validation report")
    required = security.get("required_gates")
    validated = security.get("validated_gates")
    if not isinstance(required, list) or not set(required).issubset(set(validated or [])):
        raise ReleaseFailure("security runner omitted mandatory gates from its report")

    ended_at = _utc_now()
    artifact_files = [
        (bound_closure_path, "published-source-closure", "application/json"),
        (daemon, "packaged-daemon", "application/x-executable"),
        (package_path, "packaged-install-result", "application/json"),
        (package_detail, "packaged-install-detail", "text/plain"),
        (security_report, "security-validation-result", "application/json"),
    ] + package_artifact_files + [
        (gate.log, f"{gate.name.replace(' ', '-')}-log", "text/plain") for gate in gates
    ]
    artifacts = [
        _artifact(path, run_dir, identifier, media_type)
        for path, identifier, media_type in artifact_files
    ]
    evidence = {
        "schema_version": 1,
        "evidence_id": f"wave08-release-{run_id}",
        "capability_id": "hypermid-oss-release",
        "scope": {"owner_id": "oss-release", "project_id": "hypermid"},
        "gate": "release_acceptance",
        "source_digest": source,
        "command": "PYTHONPATH=runtime python checks/hypermid/verify_wave_08.py",
        "environment": {
            "platform": platform.platform(),
            "architecture": platform.machine(),
            "daemon_version": f"sha256:{_sha256(daemon)}",
            "client_version": _client_version(),
        },
        "started_at": started_at,
        "ended_at": ended_at,
        "execution": {
            "path_kind": "real",
            "provider_live": bool(security.get("live_provider_present")),
            "explicit_opt_in": allow_live_provider,
            "product_acceptance_claim": True,
        },
        "trace": {
            "trace_id": f"wave08-{run_id}",
            "request_id": f"release-{run_id}",
        },
        "result": "passed",
        "measurements": [
            {
                "name": "published-source-closure-files",
                "observed": closure["file_count"],
                "unit": "files",
                "comparator": "gte",
                "threshold": 1,
                "passed": closure["file_count"] >= 1,
            },
            {
                "name": "mandatory-security-gates",
                "observed": len(required),
                "unit": "gates",
                "comparator": "eq",
                "threshold": 12,
                "passed": len(required) == 12,
            },
            {
                "name": "real-release-journeys",
                "observed": len(gates) - 1,
                "unit": "journeys",
                "comparator": "gte",
                "threshold": 5,
                "passed": len(gates) - 1 >= 5,
            },
            {
                "name": "deployment-release-claimed",
                "observed": False,
                "comparator": "eq",
                "threshold": False,
                "passed": True,
            },
        ],
        "errors": [],
        "artifacts": artifacts,
    }
    release_evidence = run_dir / "release-acceptance.json"
    _write_json(release_evidence, evidence)

    unqualified = [
        "Operating systems and CPU architectures other than the recorded environment.",
        "Hosted control-plane installation, rollout, deployment and upgrade paths.",
        "Physical-device and browser visual behavior beyond the exercised native lifecycle.",
        "Remote-access rendered geometry and real screen-reader runtime behavior; qualification covers non-browser component and stylesheet checks.",
    ]
    if not allow_live_provider:
        unqualified.append(
            "Live billed provider cost, latency and usage reconciliation; no operator opt-in was supplied."
        )
    report = {
        "schema_version": 1,
        "status": "passed",
        "source": {
            "digest": source,
            "revision": revision,
            "closure_digest": closure_digest,
            "closure_file_count": closure["file_count"],
            "closure_size_bytes": closure["size_bytes"],
            "closure_manifest": os.fspath(bound_closure_path.relative_to(run_dir)),
        },
        "started_at": started_at,
        "ended_at": ended_at,
        "source_checks": [
            {
                "name": "same-source security evidence",
                "status": "passed",
                "record_count": len(validated),
                "report": os.fspath(security_report.relative_to(run_dir)),
            }
        ],
        "live_journeys": [
            {
                "name": gate.name,
                "status": "passed",
                "command": list(gate.command),
                "elapsed_ms": gate.elapsed_ms,
                "log": os.fspath(gate.log.relative_to(run_dir)),
            }
            for gate in gates[:-1]
        ],
        "live_provider": {
            "requested": allow_live_provider,
            "qualified": bool(security.get("live_provider_present")),
        },
        "unqualified_paths": unqualified,
        "deployment_release": {
            "claimed": False,
            "detail": "This is local OSS release acceptance; it performs no deployment or hosted rollout.",
        },
        "release_evidence": os.fspath(release_evidence.relative_to(run_dir)),
        "release_evidence_digest": _sha256(release_evidence),
    }
    report_path = run_dir / "release-readiness.json"
    _write_json(report_path, report)
    return {"run_dir": os.fspath(run_dir), "report": report, "evidence": evidence}


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run complete local Hypermid OSS release acceptance.")
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path(os.environ.get("HYPERMID_WAVE08_ARTIFACT_DIR", DEFAULT_ARTIFACTS)),
    )
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path(os.environ.get("HYPERMID_SECURITY_EVIDENCE_DIR", DEFAULT_EVIDENCE)),
    )
    parser.add_argument("--source-digest", default=os.environ.get("HYPERMID_SOURCE_DIGEST"))
    parser.add_argument("--allow-live-provider", action="store_true")
    return parser.parse_args()


def main() -> int:
    arguments = _arguments()
    try:
        result = run_release_acceptance(
            artifact_root=arguments.artifact_dir,
            evidence_dir=arguments.evidence_dir,
            source_digest=arguments.source_digest,
            allow_live_provider=arguments.allow_live_provider,
        )
    except (OSError, ValueError, ReleaseFailure, json.JSONDecodeError) as error:
        print(f"Hypermid Wave 8 release acceptance: FAILED: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
