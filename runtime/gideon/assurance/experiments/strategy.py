"""Operator offline strategy comparisons over Gideon's isolated evaluation cells."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import secrets
import signal
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from gideon.assurance.evals import overlay, runner, store
from gideon.assurance.evals.matrix import MatrixSpec
from gideon.core.atomic_write import atomic_write
from gideon.security.sandbox import build_child_env


def _root() -> Path:
    path = store.evals_root() / "strategies"
    path.mkdir(parents=True, exist_ok=True)
    return path


def run(spec: dict) -> dict:
    if spec.get("kind") == "local_job":
        return run_local_jobs(spec)
    scenario = str(spec.get("scenario") or "")
    candidates = spec.get("candidates")
    trials = spec.get("trials", 1)
    if not scenario or not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
        raise ValueError("scenario and 1 to 8 candidates are required")
    if not isinstance(trials, int) or isinstance(trials, bool) or not 1 <= trials <= 10:
        raise ValueError("trials must be 1 to 10")
    validated = []
    names: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("every candidate needs a name")
        if item["name"] in names:
            raise ValueError("candidate names must be distinct")
        names.add(item["name"])
        component = overlay.ComponentOverlay.from_dict(item.get("component") or {})
        validated.append((item["name"], component.to_dict()))
    experiment_id = secrets.token_hex(12)
    directory = _root() / experiment_id
    directory.mkdir(mode=0o700)
    source = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    atomic_write(directory / "spec.json", source, mode=0o600)
    rows = []
    for index, (name, component) in enumerate(validated):
        matrix_id = f"strategy-{experiment_id}-{index}"
        result = runner.run_matrix(
            MatrixSpec(subject=scenario, axes={overlay.ARM_AXIS: [overlay.ARM_ON, overlay.ARM_OFF]}, trial_count=trials, component=component),
            matrix_id=matrix_id,
        )
        rows.append({"name": name, "matrix_id": matrix_id, "cells": [cell.to_dict() for cell in result.cells], "aggregates": result.aggregates})
    report = {"id": experiment_id, "created_at": datetime.now(timezone.utc).isoformat(), "spec_sha256": hashlib.sha256(source.encode()).hexdigest(), "scenario": scenario, "candidates": rows, "promotion": "human_review_required"}
    atomic_write(directory / "report.json", json.dumps(report, indent=2, sort_keys=True), mode=0o600)
    return report


def run_local_jobs(spec: dict) -> dict:
    objective = str(spec.get("objective") or "").strip()
    candidates = spec.get("candidates")
    trials = spec.get("trials", 1)
    timeout = spec.get("timeout_seconds", 60)
    if not objective or not isinstance(candidates, list) or not 1 <= len(candidates) <= 8:
        raise ValueError("objective and 1 to 8 candidates are required")
    if not isinstance(trials, int) or isinstance(trials, bool) or not 1 <= trials <= 10:
        raise ValueError("trials must be 1 to 10")
    if not isinstance(timeout, int) or isinstance(timeout, bool) or not 1 <= timeout <= 600:
        raise ValueError("timeout_seconds must be 1 to 600")
    names: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not item["name"].strip():
            raise ValueError("every candidate needs a name")
        if item["name"] in names:
            raise ValueError("candidate names must be distinct")
        names.add(item["name"])
        argv = item.get("command")
        if not isinstance(argv, list) or not 1 <= len(argv) <= 32 or not all(isinstance(arg, str) and arg and len(arg) <= 4000 for arg in argv):
            raise ValueError("candidate command must be an argv list of 1 to 32 strings")
    experiment_id = secrets.token_hex(12)
    directory = _root() / experiment_id
    directory.mkdir(mode=0o700)
    source = json.dumps(spec, sort_keys=True, separators=(",", ":"))
    atomic_write(directory / "spec.json", source, mode=0o600)
    report = {"id": experiment_id, "kind": "local_job", "objective": objective,
              "created_at": datetime.now(timezone.utc).isoformat(),
              "spec_sha256": hashlib.sha256(source.encode()).hexdigest(),
              "attempts": [], "promotion": "human_review_required"}
    _write_report(directory, report)
    for index, item in enumerate(candidates):
        for trial in range(trials):
            work = directory / f"attempt-{index:02d}-{trial:02d}"
            work.mkdir(mode=0o700)
            home = work / "home"
            home.mkdir(mode=0o700)
            env = build_child_env(site="evals-cell", extra={"GIDEON_WORKSPACE": str(work), "GIDEON_HOME": str(home)})
            attempt = {"candidate": item["name"], "trial": trial, "command": item["command"],
                       "workspace": str(work), "status": "running", "started_at": datetime.now(timezone.utc).isoformat()}
            report["attempts"].append(attempt)
            _write_report(directory, report)
            started = time.monotonic()
            try:
                with (work / "stdout.log").open("wb") as out, (work / "stderr.log").open("wb") as err:
                    proc = subprocess.Popen(item["command"], cwd=work, env=env,
                                            stdout=out, stderr=err, start_new_session=True)
                    try:
                        returncode = proc.wait(timeout=timeout)
                        status = "complete" if returncode == 0 else "failed"
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(proc.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                        proc.wait()
                        status, returncode = "timeout", None
                stdout = _tail(work / "stdout.log")
                stderr = _tail(work / "stderr.log")
            except OSError as exc:
                stdout, stderr = "", f"{type(exc).__name__}: {exc}"
                status, returncode = "spawn_failed", None
            score: float | None = 1.0 if status == "complete" else None
            valid = status == "complete"
            for line in reversed(stdout.splitlines()):
                if not line.startswith("GIDEON_STRATEGY_RESULT "):
                    continue
                try:
                    result = json.loads(line.removeprefix("GIDEON_STRATEGY_RESULT "))
                    candidate_score = float(result["score"])
                    if not isinstance(result.get("valid"), bool) or not math.isfinite(candidate_score):
                        raise ValueError("invalid score or validity")
                    score = candidate_score if status == "complete" else None
                    valid = bool(result["valid"]) and status == "complete"
                except (ValueError, TypeError, KeyError):
                    status, valid, score = "invalid_result", False, None
                break
            attempt.update(status=status, returncode=returncode, score=score, valid=valid,
                           elapsed_seconds=round(time.monotonic() - started, 3),
                           stdout_tail=stdout[-4000:], stderr_tail=stderr[-4000:],
                           completed_at=datetime.now(timezone.utc).isoformat())
            _write_report(directory, report)
    return report


def _write_report(directory: Path, report: dict) -> None:
    atomic_write(directory / "report.json", json.dumps(report, indent=2, sort_keys=True), mode=0o600)


def _tail(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        stream.seek(max(0, stream.tell() - 4000))
        return stream.read().decode("utf-8", errors="replace")


def main() -> None:
    parser = argparse.ArgumentParser(description="Gideon offline strategy workspace")
    sub = parser.add_subparsers(dest="command", required=True)
    run_cmd = sub.add_parser("run")
    run_cmd.add_argument("spec", type=Path)
    show_cmd = sub.add_parser("show")
    show_cmd.add_argument("id")
    sub.add_parser("list")
    args = parser.parse_args()
    if args.command == "run":
        print(json.dumps(run(json.loads(args.spec.read_text())), indent=2))
    elif args.command == "list":
        print(json.dumps([p.name for p in sorted(_root().iterdir()) if (p / "report.json").is_file()], indent=2))
    else:
        if len(args.id) != 24 or any(c not in "0123456789abcdef" for c in args.id):
            raise ValueError("invalid experiment ID")
        print((_root() / args.id / "report.json").read_text())


if __name__ == "__main__":
    main()
