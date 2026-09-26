"""Repository round admission, scoped changes, verification and durable handoffs."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from gideon.automation.workflows import store
from gideon.automation.workflows.models import TERMINAL_RUN_STATUSES


@dataclass(frozen=True)
class RoundDecision:
    allow_next: bool
    reason: str = ""
    handoff: dict[str, Any] = field(default_factory=dict)


def has_round_protocol(spec: dict | None) -> bool:
    if not isinstance(spec, dict):
        return False
    if spec.get("kind") == "loop" and isinstance((spec.get("config") or {}).get("round_protocol"), dict):
        return True
    return any(
        has_round_protocol(child)
        for child in [*(spec.get("children") or []), spec.get("body"),
                      *(spec.get("cases") or {}).values(), spec.get("default"), spec.get("root")]
    )


def _workspace(config: dict, inputs: dict) -> Path:
    raw = config.get("worktree") or inputs.get("worktree") or ""
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("round protocol requires an isolated worktree path")
    path = Path(raw).expanduser().resolve()
    if not path.is_dir():
        raise ValueError("round worktree does not exist")
    result = _git(path, "rev-parse", "--show-toplevel")
    if Path(result.stdout.strip()).resolve() != path:
        raise ValueError("round worktree must be the repository root")
    git_dir = Path(_git(path, "rev-parse", "--git-dir").stdout.strip())
    git_dir = (path / git_dir).resolve() if not git_dir.is_absolute() else git_dir.resolve()
    if git_dir == path / ".git":
        raise ValueError("round protocol requires a linked, isolated git worktree")
    return path


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, timeout=30
    )


def _lock_path(path: Path) -> Path:
    digest = hashlib.sha256(str(path).encode()).hexdigest()[:24]
    directory = store.runs_root() / ".round-locks"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{digest}.json"


def _roles(config: dict) -> list[dict]:
    roles = config.get("roles")
    if not isinstance(roles, list) or len(roles) < 2:
        raise ValueError("round protocol requires at least two declared roles")
    names = [str(role.get("name") or "").strip() for role in roles if isinstance(role, dict)]
    if len(names) != len(roles) or not all(names) or len(set(names)) != len(names):
        raise ValueError("round roles must have unique nonempty names")
    if not all(isinstance(role.get("allowed_paths"), list) and role["allowed_paths"] for role in roles):
        raise ValueError("every round role must declare allowed_paths")
    return roles


def _pid_alive(pid: object) -> bool:
    try:
        os.kill(int(pid), 0)
        return True
    except (OSError, TypeError, ValueError):
        return False


def admit_round(run_id: str, config: dict, inputs: dict) -> RoundDecision:
    try:
        _roles(config)
        path = _workspace(config, inputs)
        branch = str(config.get("branch") or "").strip()
        if not branch:
            raise ValueError("round protocol requires a reviewed branch name")
        if _git(path, "branch", "--show-current").stdout.strip() != branch:
            raise ValueError(f"round worktree must be on reviewed branch {branch}")
        max_rounds = int(config.get("max_rounds") or 0)
        if not 1 <= max_rounds <= 100:
            raise ValueError("max_rounds must be between 1 and 100")
        command = str(config.get("verify_command") or inputs.get("verify_command") or "").strip()
        if not command and not all(str(role.get("verify_command") or "").strip() for role in _roles(config)):
            raise ValueError("round verification command is required for every role")
        max_handbacks = int(config.get("max_handbacks") or 0)
        if max_handbacks < 0 or max_handbacks > max_rounds:
            raise ValueError("max_handbacks must be within the round limit")
        if max_handbacks and str(config.get("handback_role") or "") not in {role["name"] for role in _roles(config)}:
            raise ValueError("handback_role must name a declared role")
        lock = _lock_path(path)
        while True:
            try:
                with lock.open("x", encoding="utf-8") as handle:
                    json.dump({"run_id": run_id, "worktree": str(path), "pid": os.getpid()}, handle)
                break
            except FileExistsError:
                try:
                    lock_record = json.loads(lock.read_text(encoding="utf-8"))
                    owner = lock_record.get("run_id")
                except (OSError, ValueError):
                    raise ValueError("round worktree lock is unreadable") from None
                if owner == run_id:
                    break
                existing = store.get(str(owner)) if owner else None
                if (existing is not None and existing.status in TERMINAL_RUN_STATUSES) or (existing is None and not _pid_alive(lock_record.get("pid"))):
                    lock.unlink(missing_ok=True)
                    continue
                raise ValueError(f"worktree is already owned by active round run {owner}")
        first = _roles(config)[0]
        return RoundDecision(True, handoff={
            "worktree": str(path), "role": first["name"],
            "allowed_paths": first["allowed_paths"],
            "instruction": "Work only within the active role's allowed_paths and leave a concrete handoff.",
        })
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return RoundDecision(False, str(error))


def release_round(run_id: str, config: dict, inputs: dict) -> None:
    try:
        lock = _lock_path(_workspace(config, inputs))
        owner = json.loads(lock.read_text(encoding="utf-8")).get("run_id")
        if owner == run_id:
            lock.unlink(missing_ok=True)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass


def _records(run_id: str) -> list[dict]:
    path = store.run_dir(run_id) / "rounds.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [record for line in lines if (record := json.loads(line))]


def read_rounds(run_id: str) -> list[dict]:
    """Bounded, user-facing journal projection; internal hashes stay private."""
    try:
        records = _records(run_id)[-100:]
    except (OSError, ValueError):
        return []
    return [
        {
            "iteration": row.get("iteration"),
            "at": row.get("at"),
            "allow_next": row.get("allow_next"),
            "reason": row.get("reason"),
            "completed_role": handoff.get("completed_role"),
            "next_role": handoff.get("next_role"),
            "changed_paths": handoff.get("changed_paths", []),
            "quarantined_paths": handoff.get("quarantined_paths", []),
            "quarantine_evidence": _quarantine_evidence(run_id, row.get("iteration"), handoff.get("quarantined_paths", [])),
            "handback": bool(handoff.get("handback")),
            "verification": {"exit_code": (handoff.get("verification") or {}).get("exit_code")},
        }
        for row in records
        for handoff in [row.get("handoff") or {}]
    ]


def _quarantine_evidence(run_id: str, iteration: object, paths: list[str]) -> list[dict]:
    directory = store.run_dir(run_id) / "round-quarantine" / str(iteration)
    evidence = []
    for name in paths[:5]:
        target = directory / name
        try:
            if target.is_symlink():
                evidence.append({"path": name, "content": f"symlink → {os.readlink(target)}", "truncated": False})
            elif target.is_file():
                with target.open("rb") as handle:
                    content = handle.read(4097)
                evidence.append({"path": name, "content": content[:4096].decode("utf-8", errors="replace"),
                                 "truncated": len(content) > 4096})
        except OSError:
            continue
    return evidence


def completed_iterations(run_id: str, loop_path: str) -> int:
    """Restore the next iteration from the durable journal after a host restart."""
    try:
        rows = _records(run_id)
    except (OSError, ValueError):
        return 0
    return 1 + max((int(row["iteration"]) for row in rows
                    if row.get("loop_path", "root") == loop_path and row.get("allow_next")), default=-1)


def _append(run_id: str, record: dict) -> None:
    path = store.run_dir(run_id) / "rounds.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(record, separators=(",", ":"), default=str) + "\n").encode()
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_APPEND, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _changed(path: Path) -> list[str]:
    tracked = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", "HEAD"],
        cwd=path, check=True, capture_output=True, timeout=30,
    ).stdout.decode().split("\0")
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=path, check=True, capture_output=True, timeout=30,
    ).stdout.decode().split("\0")
    return sorted({name for name in [*tracked, *untracked] if name})


def _fingerprint(path: Path, name: str) -> str:
    diff = subprocess.run(
        ["git", "diff", "--binary", "--no-renames", "HEAD", "--", name],
        cwd=path, check=True, capture_output=True, timeout=30,
    ).stdout
    if diff:
        return hashlib.sha256(diff).hexdigest()
    tracked = subprocess.run(
        ["git", "ls-files", "--error-unmatch", "--", name],
        cwd=path, capture_output=True, timeout=30,
    ).returncode == 0
    target = path / name
    if tracked or not target.exists() and not target.is_symlink():
        return ""
    content = os.readlink(target).encode() if target.is_symlink() else target.read_bytes()
    return hashlib.sha256(content).hexdigest()


def _snapshot(path: Path, run_id: str, iteration: int, changed: list[str]) -> None:
    directory = store.run_dir(run_id) / "round-baselines" / str(iteration)
    directory.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, bool] = {}
    for name in changed:
        source = path / name
        exists = source.is_file() or source.is_symlink()
        manifest[name] = exists
        if exists:
            target = directory / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target, follow_symlinks=False)
            if target.is_file() and not target.is_symlink():
                target.chmod(0o600)
    manifest_path = directory.parent / f"{iteration}.json"
    descriptor = os.open(manifest_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)


def _quarantine(path: Path, run_id: str, iteration: int, forbidden: list[str]) -> list[str]:
    directory = store.run_dir(run_id) / "round-quarantine" / str(iteration)
    baseline_dir = store.run_dir(run_id) / "round-baselines" / str(iteration - 1)
    try:
        manifest = json.loads((baseline_dir.parent / f"{iteration - 1}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        manifest = {}
    saved: list[str] = []
    for name in forbidden:
        target = path / name
        destination = directory / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        patch = subprocess.run(
            ["git", "diff", "--binary", "--no-renames", "HEAD", "--", name],
            cwd=path, check=True, capture_output=True, timeout=30,
        ).stdout
        if patch:
            patch_path = destination.with_suffix(destination.suffix + ".patch")
            descriptor = os.open(patch_path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(patch)
        if target.is_file() or target.is_symlink():
            shutil.copy2(target, destination, follow_symlinks=False)
            if destination.is_file() and not destination.is_symlink():
                destination.chmod(0o600)
        tracked = subprocess.run(
            ["git", "cat-file", "-e", f"HEAD:{name}"],
            cwd=path, capture_output=True, timeout=30,
        ).returncode == 0
        if tracked:
            _git(path, "restore", "--source=HEAD", "--staged", "--worktree", "--", name)
        else:
            subprocess.run(["git", "rm", "--cached", "--ignore-unmatch", "--", name], cwd=path, check=True, capture_output=True, timeout=30)
            target.unlink(missing_ok=True)
        if name in manifest:
            target.unlink(missing_ok=True)
            if manifest[name]:
                source = baseline_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target, follow_symlinks=False)
        saved.append(name)
    return saved


def complete_round(*, run_id: str, iteration: int, config: dict, inputs: dict, output: object, loop_path: str = "root") -> RoundDecision:
    try:
        previous = next((row for row in _records(run_id) if row.get("iteration") == iteration and row.get("loop_path", "root") == loop_path), None)
        if previous is not None:
            return RoundDecision(bool(previous["allow_next"]), str(previous.get("reason") or ""), dict(previous.get("handoff") or {}))
        path = _workspace(config, inputs)
        roles = _roles(config)
        preceding = next((row for row in reversed(_records(run_id)) if row.get("iteration") == iteration - 1 and row.get("loop_path", "root") == loop_path), None)
        preceding_role = str((preceding or {}).get("handoff", {}).get("next_role") or "")
        active_role = next((role for role in roles if role["name"] == preceding_role), roles[iteration % len(roles)])
        active_index = roles.index(active_role)
        next_role = roles[(active_index + 1) % len(roles)]
        allowed = [str(pattern) for pattern in active_role["allowed_paths"]]
        previous_hashes = dict((preceding or {}).get("handoff", {}).get("path_hashes") or {})
        candidates = sorted(set(_changed(path)) | set(previous_hashes))
        touched = [name for name in candidates if _fingerprint(path, name) != previous_hashes.get(name, "")]
        forbidden = [name for name in touched if not any(fnmatch.fnmatch(name, pattern) for pattern in allowed)]
        quarantined = _quarantine(path, run_id, iteration, forbidden) if forbidden else []
        command = str(active_role.get("verify_command") or config.get("verify_command") or inputs.get("verify_command") or "").strip()
        if not command:
            raise ValueError("round verification command is required")
        check = subprocess.run(
            ["bash", "-lc", command], cwd=path, capture_output=True, text=True,
            timeout=max(1, min(int(config.get("verify_timeout_secs") or 300), 3600)),
        )
        max_rounds = int(config["max_rounds"])
        stop_marker = str(config.get("stop_marker") or "").strip()
        stopped = bool(stop_marker and stop_marker in str(output))
        handbacks_used = sum(1 for row in _records(run_id) if row.get("loop_path", "root") == loop_path and (row.get("handoff") or {}).get("handback"))
        can_handback = (check.returncode != 0 and not quarantined
                        and handbacks_used < int(config.get("max_handbacks") or 0)
                        and iteration + 1 < max_rounds)
        if can_handback:
            next_role = next(role for role in roles if role["name"] == config["handback_role"])
        allow = (check.returncode == 0 or can_handback) and not quarantined
        reason = "out-of-bound changes quarantined" if quarantined else (
            f"verification failed ({check.returncode}); handback to {next_role['name']} ({handbacks_used + 1}/{config['max_handbacks']})" if can_handback else
            f"verification failed ({check.returncode})" if check.returncode else (
                "stop marker reached" if stopped else "maximum rounds reached" if iteration + 1 >= max_rounds else "continue"
            )
        )
        handoff = {
            "completed_role": active_role["name"],
            "next_role": next_role["name"],
            "next_allowed_paths": next_role["allowed_paths"],
            "changed_paths": touched,
            "path_hashes": {name: _fingerprint(path, name) for name in _changed(path)},
            "quarantined_paths": quarantined,
            "handback": can_handback,
            "verification": {"command": command, "exit_code": check.returncode, "stdout": check.stdout[-4000:], "stderr": check.stderr[-4000:]},
            "stop": stopped or iteration + 1 >= max_rounds,
            "output": str(output)[:4000],
        }
        if allow:
            _snapshot(path, run_id, iteration, _changed(path))
        _append(run_id, {"loop_path": loop_path, "iteration": iteration, "at": datetime.now(timezone.utc).isoformat(), "allow_next": allow, "reason": reason, "handoff": handoff})
        return RoundDecision(allow, reason, handoff)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        return RoundDecision(False, str(error))
