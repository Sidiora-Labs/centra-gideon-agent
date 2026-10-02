from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time

from checks.hypermid.evidence import ObservationWriter


ROOT = Path(__file__).resolve().parents[2]
FLEET = ROOT.parent
TARGET = Path(os.environ.get("CARGO_TARGET_DIR", FLEET / "build/target"))
DAEMON = Path(os.environ.get("HYPERMID_DAEMON_BINARY", TARGET / "debug/hypermid-daemon"))
RUST_HARNESS = ROOT / "checks/hypermid/sdk_harness/rust/Cargo.toml"
TYPESCRIPT_HARNESS = ROOT / "checks/hypermid/sdk_harness/typescript.ts"
TYPESCRIPT_CLIENT = ROOT / "clients/hypermid-ts/src/client.ts"
TYPESCRIPT_NODE = ROOT / "clients/hypermid-ts/src/node.ts"
TSC = ROOT / "node_modules/.bin/tsc"
SWIFT_HARNESS = ROOT / "checks/hypermid/sdk_harness/swift"
SWIFT_IMAGE = "swift@sha256:fc2fe3b78e138702f0d69fcf1420372eb044194450c8eb6ba778ab1c87ff7b1b"


def _run(command: list[str], *, timeout: int = 180, environment: dict[str, str] | None = None) -> dict:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"SDK harness failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    lines = [line for line in completed.stdout.splitlines() if line.startswith("{")]
    if not lines:
        raise AssertionError(f"SDK harness returned no JSON result: {completed.stdout}")
    return json.loads(lines[-1])


def _wait_for_record(process: subprocess.Popen[str], record: Path) -> None:
    deadline = time.monotonic() + 10
    while not record.is_file():
        if process.poll() is not None:
            stderr = process.stderr.read() if process.stderr is not None else ""
            raise AssertionError(f"daemon exited before readiness: {stderr}")
        if time.monotonic() >= deadline:
            raise AssertionError("daemon connection record timed out")
        time.sleep(0.01)


def _compile(command: list[str], environment: dict[str, str], label: str, timeout: int = 180) -> None:
    completed = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{label} failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def test_rust_typescript_and_swift_sdks_against_real_tls_daemon(tmp_path: Path) -> None:
    environment = os.environ.copy()
    environment["CARGO_BUILD_JOBS"] = "2"
    environment["CARGO_TARGET_DIR"] = str(TARGET)
    if "HYPERMID_DAEMON_BINARY" not in os.environ:
        _compile(
            [str(Path.home() / ".cargo/bin/cargo"), "build", "-p", "hypermid-daemon"],
            environment,
            "daemon build",
            timeout=300,
        )
    assert DAEMON.is_file() and os.access(DAEMON, os.X_OK)
    socket = tmp_path / "hypermid.sock"
    record = tmp_path / "connection.json"
    capability_expires_ms = int(time.time() * 1_000) + 3_600_000
    daemon = subprocess.Popen(
        [
            str(DAEMON), "--socket", str(socket), "--connection-record", str(record),
            "--local-credential-id", "sdk-credential",
            "--local-owner-id", "sdk-owner",
            "--local-project-id", "sdk-project",
            "--local-workspace-id", "sdk-workspace",
            "--local-capability-id", "sdk-capability",
            "--local-capability-operation", "read",
            "--local-capability-operation", "administer",
            "--local-capability-resource", "control",
            "--local-capability-expires-ms", str(capability_expires_ms),
        ],
        cwd=ROOT,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_record(daemon, record)
        invalid_record = tmp_path / "invalid-version.json"
        invalid = json.loads(record.read_text(encoding="utf-8"))
        invalid["protocol"] = "hypermid.v0"
        invalid_record.write_text(json.dumps(invalid, separators=(",", ":")), encoding="utf-8")
        invalid_record.chmod(0o600)

        typescript_build = tmp_path / "typescript-build"
        typescript_build.mkdir()
        (typescript_build / "package.json").write_text('{"type":"module"}\n', encoding="utf-8")
        _compile(
            [
                str(TSC), "--outDir", str(typescript_build), "--rootDir", str(ROOT),
                "--target", "ES2022", "--module", "ES2022", "--moduleResolution", "Bundler",
                "--strict", "--types", "node", str(TYPESCRIPT_CLIENT), str(TYPESCRIPT_NODE),
                str(TYPESCRIPT_HARNESS),
            ],
            environment,
            "TypeScript SDK harness compile",
        )
        compiled_typescript_harness = typescript_build / "checks/hypermid/sdk_harness/typescript.js"
        results = [
            _run(
                [
                    str(Path.home() / ".cargo/bin/cargo"), "run", "--quiet",
                    "--manifest-path", str(RUST_HARNESS), "--", str(record), str(invalid_record),
                ],
                environment=environment,
            ),
            _run(["node", str(compiled_typescript_harness), str(record), str(invalid_record)]),
        ]

        swift_build = FLEET / "build/swift-sdk-harness"
        swift_build.mkdir(parents=True, exist_ok=True)
        results.append(
            _run(
                [
                    "docker", "run", "--rm",
                    "-u", f"{os.getuid()}:{os.getgid()}",
                    "-e", "HOME=/tmp/swift-home",
                    "-v", f"{ROOT}:/work",
                    "-v", f"{tmp_path}:{tmp_path}",
                    "-v", f"{swift_build}:/swift-build",
                    "-w", "/work/checks/hypermid/sdk_harness/swift",
                    SWIFT_IMAGE,
                    "swift", "run", "--scratch-path", "/swift-build",
                    "HypermidSwiftSDKHarness", str(record), str(invalid_record),
                ],
                timeout=300,
            )
        )
    finally:
        if daemon.poll() is None:
            daemon.terminate()
            try:
                daemon.wait(timeout=5)
            except subprocess.TimeoutExpired:
                daemon.kill()
                daemon.wait(timeout=5)

    expected_languages = {"rust", "typescript", "swift"}
    compatibility_checks = (
        "tls",
        "describe",
        "passthrough",
        "durable_ack",
        "reconnect_resume",
        "wrong_epoch_refused",
        "invalid_version_refused",
        "mutation_outcome_unknown",
    )
    qualified_languages = {
        result.get("language")
        for result in results
        if result.get("language") in expected_languages
        and all(result.get(check) is True for check in compatibility_checks)
    }
    wrong_epoch_acceptances = sum(
        result.get("wrong_epoch_refused") is not True for result in results
    )
    invalid_version_acceptances = sum(
        result.get("invalid_version_refused") is not True for result in results
    )
    outcome_unknown_preserved = (
        {result.get("language") for result in results} == expected_languages
        and all(result.get("mutation_outcome_unknown") is True for result in results)
    )

    observation = ObservationWriter.from_env("client_compatibility")
    if observation is not None:
        matrix = tmp_path / "sdk-compatibility-matrix.json"
        matrix.write_text(
            json.dumps(
                {
                    "gate": "client_compatibility",
                    "languages": sorted(results, key=lambda result: result["language"]),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        observation.measure(
            "qualified-sdk-languages",
            len(qualified_languages),
            "gte",
            3,
            "languages",
        )
        observation.measure(
            "wrong-epoch-acceptances",
            wrong_epoch_acceptances,
            "eq",
            0,
            "acceptances",
        )
        observation.measure(
            "invalid-version-acceptances",
            invalid_version_acceptances,
            "eq",
            0,
            "acceptances",
        )
        observation.measure(
            "outcome-unknown-preserved",
            outcome_unknown_preserved,
            "eq",
            True,
            "boolean",
        )
        observation.artifact(
            "sdk-compatibility-matrix", matrix, "application/json"
        )
        observation.finish(source_digest=os.environ["HYPERMID_SOURCE_DIGEST"])

    assert {result["language"] for result in results} == {"rust", "typescript", "swift"}
    for result in results:
        assert result == {
            "language": result["language"],
            "tls": True,
            "describe": True,
            "passthrough": True,
            "durable_ack": True,
            "reconnect_resume": True,
            "wrong_epoch_refused": True,
            "invalid_version_refused": True,
            "mutation_outcome_unknown": True,
        }
