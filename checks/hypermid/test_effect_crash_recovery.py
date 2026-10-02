from __future__ import annotations

import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from checks.hypermid.evidence import ObservationWriter


SERVICE = Path(__file__).with_name("effect_service.py")
CASES = (
    ("before_intent", "daemon", "runner"),
    ("after_intent", "adapter", "runner"),
    ("during_dispatch", "sandbox", "service"),
    ("after_external_ack", "daemon", "runner"),
    ("before_result_commit", "adapter", "runner"),
    ("after_result_commit", "sandbox", "runner"),
)


def _wait_for(path: Path, process: subprocess.Popen[str], timeout: float = 8.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"process exited before {path.name}: code={process.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {path}")


def _start_service(root: Path, pause_at: str | None = None) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        str(SERVICE),
        "serve",
        "--socket",
        str(root / "effect.sock"),
        "--database",
        str(root / "service.sqlite3"),
        "--markers",
        str(root / "markers"),
    ]
    if pause_at is not None:
        command.extend(["--pause-at", pause_at])
    process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                f"service exited before readiness: code={process.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(0.2)
                client.connect(str(root / "effect.sock"))
                client.sendall(b'{"operation":"ping"}\n')
                response = json.loads(client.makefile("rb").readline())
                if response == {"state": "ready"}:
                    return process
        except (FileNotFoundError, ConnectionRefusedError, TimeoutError, OSError):
            time.sleep(0.01)
    raise AssertionError("timed out waiting for effect service readiness")


def _start_runner(
    root: Path,
    role: str,
    effect_id: str,
    digest: str,
    pause_at: str | None = None,
) -> subprocess.Popen[str]:
    command = [
        sys.executable,
        str(SERVICE),
        "run",
        "--socket",
        str(root / "effect.sock"),
        "--ledger",
        str(root / "ledger.sqlite3"),
        "--markers",
        str(root / "markers"),
        "--role",
        role,
        "--effect-id",
        effect_id,
        "--input-digest",
        digest,
        "--result",
        f"receipt:{effect_id}",
    ]
    if pause_at is not None:
        command.extend(["--pause-at", pause_at])
    return subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _command(root: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SERVICE), *arguments],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=10,
        check=check,
    )


def _recover(root: Path, role: str, effect_id: str, digest: str) -> dict[str, Any]:
    completed = _command(
        root,
        "recover",
        "--socket",
        str(root / "effect.sock"),
        "--ledger",
        str(root / "ledger.sqlite3"),
        "--role",
        role,
        "--effect-id",
        effect_id,
        "--input-digest",
        digest,
    )
    return json.loads(completed.stdout)


def _inspect(root: Path, effect_id: str) -> dict[str, Any]:
    completed = _command(
        root,
        "inspect",
        "--database",
        str(root / "service.sqlite3"),
        "--ledger",
        str(root / "ledger.sqlite3"),
        "--effect-id",
        effect_id,
    )
    return json.loads(completed.stdout)


def _finish(process: subprocess.Popen[str]) -> subprocess.CompletedProcess[str]:
    stdout, stderr = process.communicate(timeout=10)
    return subprocess.CompletedProcess(process.args, process.returncode, stdout, stderr)


def _stop(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def test_real_process_effect_crash_matrix_preserves_truth_and_idempotency(tmp_path: Path) -> None:
    writer = ObservationWriter.from_env("effect_recovery")
    boundary_ledger: list[dict[str, Any]] = []
    killed_pids: set[int] = set()
    for index, (stage, role, kill_target) in enumerate(CASES, start=1):
        root = tmp_path / f"{index:02d}-{stage}"
        root.mkdir()
        effect_id = f"effect-{stage}"
        digest = hashlib.sha256(f"input:{stage}".encode()).hexdigest()
        service: subprocess.Popen[str] | None = None
        runner: subprocess.Popen[str] | None = None
        recovery_observations: list[dict[str, Any]] = []
        try:
            service = _start_service(root, pause_at="during_dispatch" if kill_target == "service" else None)
            runner = _start_runner(
                root,
                role,
                effect_id,
                digest,
                pause_at=stage if kill_target == "runner" else None,
            )
            component = "service" if kill_target == "service" else role
            marker = root / "markers" / f"{component}-{stage}.ready"
            target = service if kill_target == "service" else runner
            _wait_for(marker, target)
            killed_pids.add(target.pid)
            target.kill()
            target.wait(timeout=5)
            assert target.returncode < 0
            kill_returncode = target.returncode

            if kill_target == "service":
                completed = _finish(runner)
                assert completed.returncode == 3, completed.stderr
                dispatch_outcome = json.loads(completed.stdout)
                assert dispatch_outcome["state"] == "unknown"
                recovery = _recover(root, role, effect_id, digest)
                recovery_observations.append(
                    {
                        "phase": "service_unavailable",
                        "state": recovery["state"],
                        "source": recovery["source"],
                        "requires_unknown": True,
                    }
                )
                assert recovery["state"] == "unknown"
                before_retry = _inspect(root, effect_id)
                blocked = _start_runner(root, role, effect_id, digest)
                blocked_result = _finish(blocked)
                assert blocked_result.returncode == 4
                blocked_outcome = json.loads(blocked_result.stdout)
                assert blocked_outcome["state"] == "unknown"
                recovery_observations.append(
                    {
                        "phase": "automatic_retry_blocked",
                        "state": blocked_outcome["state"],
                        "requires_unknown": True,
                    }
                )
                assert _inspect(root, effect_id)["attempt_count"] == before_retry["attempt_count"]
                service = _start_service(root)
                recovery = _recover(root, role, effect_id, digest)
                recovery_observations.append(
                    {
                        "phase": "authoritative_service_reconciliation",
                        "state": recovery["state"],
                        "source": recovery["source"],
                        "requires_unknown": False,
                    }
                )
                assert recovery["state"] == "not_started"
                runner = _start_runner(root, role, effect_id, digest)
                completed = _finish(runner)
                assert completed.returncode == 0, completed.stderr
            else:
                recovery = _recover(root, role, effect_id, digest)
                recovery_observations.append(
                    {
                        "phase": "post_kill_recovery",
                        "state": recovery["state"],
                        "source": recovery["source"],
                        "requires_unknown": False,
                    }
                )
                assert recovery["state"] in {"not_started", "committed"}
                if recovery["state"] == "not_started":
                    runner = _start_runner(root, role, effect_id, digest)
                    completed = _finish(runner)
                    assert completed.returncode == 0, completed.stderr

            final_recovery = _recover(root, role, effect_id, digest)
            recovery_observations.append(
                {
                    "phase": "terminal_recovery",
                    "state": final_recovery["state"],
                    "source": final_recovery["source"],
                    "requires_unknown": False,
                }
            )
            assert final_recovery["state"] == "committed"
            snapshot = _inspect(root, effect_id)
            assert snapshot["committed_rows"] == 1
            assert snapshot["commit_count"] == 1
            assert snapshot["ledger"]["state"] == "committed"
            assert snapshot["service"]["result"] == f"receipt:{effect_id}"
            boundary_ledger.append(
                {
                    "effect_id": effect_id,
                    "stage": stage,
                    "role": role,
                    "kill_target": kill_target,
                    "kill_returncode": kill_returncode,
                    "recoveries": recovery_observations,
                    "attempt_count": snapshot["attempt_count"],
                    "committed_rows": snapshot["committed_rows"],
                    "commit_count": snapshot["commit_count"],
                    "ledger_state": snapshot["ledger"]["state"],
                    "service_state": snapshot["service"]["state"],
                }
            )
        finally:
            _stop(runner)
            _stop(service)

    assert len(killed_pids) == len(CASES)
    assert [item["stage"] for item in boundary_ledger] == [item[0] for item in CASES]
    assert all(item["commit_count"] == 1 for item in boundary_ledger)
    during_dispatch = next(
        item for item in boundary_ledger if item["stage"] == "during_dispatch"
    )
    assert [item["state"] for item in during_dispatch["recoveries"]] == [
        "unknown",
        "unknown",
        "not_started",
        "committed",
    ]
    kill_boundaries = [
        item for item in boundary_ledger if int(item["kill_returncode"]) < 0
    ]
    duplicated_committed_effects = sorted(
        {
            str(item["effect_id"])
            for item in boundary_ledger
            if int(item["committed_rows"]) > 1 or int(item["commit_count"]) > 1
        }
    )
    unknown_effects_labeled_failed = [
        {
            "effect_id": item["effect_id"],
            "phase": recovery["phase"],
        }
        for item in boundary_ledger
        for recovery in item["recoveries"]
        if recovery["requires_unknown"] and recovery["state"] == "failed"
    ]
    artifact = tmp_path / "effect-boundary-ledger.json"
    artifact.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "boundaries": boundary_ledger,
                "observations": {
                    "effect_kill_boundaries": len(kill_boundaries),
                    "duplicated_committed_effect_ids": duplicated_committed_effects,
                    "unknown_effects_labeled_failed": unknown_effects_labeled_failed,
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    if writer is not None:
        writer.measure(
            "effect-kill-boundaries",
            len(kill_boundaries),
            "gt",
            0,
            "boundaries",
        )
        writer.measure(
            "duplicated-committed-effects",
            len(duplicated_committed_effects),
            "eq",
            0,
            "effects",
        )
        writer.measure(
            "unknown-effects-labeled-failed",
            len(unknown_effects_labeled_failed),
            "eq",
            0,
            "effects",
        )
        writer.artifact(
            "effect-boundary-ledger",
            artifact,
            "application/json",
        )
        writer.finish(source_digest=os.environ["HYPERMID_SOURCE_DIGEST"])
