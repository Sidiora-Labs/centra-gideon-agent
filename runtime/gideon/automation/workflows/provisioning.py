"""Managed workspace effects, durable setup execution and run-record projection."""

from __future__ import annotations

import errno
import fcntl
import logging
import os
import shlex
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.automation.workflows import worktrees
from gideon.automation.workflows.workspace import (
    Mode,
    SpecIssue,
    WorkspaceSpec,
    parse_workspace,
    plan_provisioning,
)

logger = logging.getLogger(__name__)
STEP_TIMEOUT_SECS = 600.0
WORKSPACE_KEY = "workspace"
WORKTREE_PATH_KEY = "worktree_path"
PRESERVED_PATH_KEY = "preserved_workspace_path"
_DURABLE_STEP_SH = (
    'rc="$1"; out="$2"; shift 2; "$@" >"$out" 2>&1; printf %s "$?" >"$rc"'
)
_DURABLE_POLL_SECS = 0.3


@dataclass
class WorkspaceLock:
    """An acquired workspace lock, or an honest refusal.

    `held_by` names the live PID on a refusal so the message can say WHO, not just "busy". A
    contention message with no owner is one a user cannot act on.
    """

    acquired: bool
    path: str = ""
    held_by: int = 0
    reason: str = ""
    _fd: Any = field(default=None, repr=False, compare=False)

    def release(self) -> None:
        descriptor = self._fd
        if descriptor is None:
            return
        actions = (
            lambda: fcntl.flock(descriptor, fcntl.LOCK_UN),
            lambda: descriptor.close(),
        )
        for index, action in enumerate(actions):
            try:
                action()
            except OSError:
                if index == 0:
                    logger.debug(
                        "workspace lock unlock failed for %s", self.path, exc_info=True
                    )
        self._fd = None


@dataclass
class Provisioned:
    """What provisioning actually produced, for the run record and the cockpit.

    `ok=False` with `fatal=True` is a REFUSAL — the spec declared something that cannot be
    honored (an unknown mode, a greedy preserve pattern), and running anyway would run in a mode
    nobody chose. Everything else degrades: a worktree that could not be created falls back to
    the project workspace WITH the reason recorded, because refusing a run because git is
    unavailable would make `mode: worktree` unusable on a non-repo workspace.
    """

    mode: Mode = Mode.SCRATCH
    path: str = ""
    branch: str = ""
    isolated: bool = False
    preserved: list[str] = field(default_factory=list)
    preserve_skipped: list[str] = field(default_factory=list)
    setup_ran: list[str] = field(default_factory=list)
    setup_skipped: list[str] = field(default_factory=list)
    setup_failed: list[str] = field(default_factory=list)
    issues: list[SpecIssue] = field(default_factory=list)
    degraded_reason: str = ""
    ok: bool = True
    container_id: str = ""
    container_backend: str = ""

    @property
    def fatal(self) -> bool:
        return any(i.fatal for i in self.issues)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "path": self.path,
            "branch": self.branch,
            "isolated": self.isolated,
            "preserved": list(self.preserved),
            "preserve_skipped": list(self.preserve_skipped),
            "setup": {
                "ran": list(self.setup_ran),
                "skipped": list(self.setup_skipped),
                "failed": list(self.setup_failed),
                "blocked_run": False,
            },
            "issues": [i.to_dict() for i in self.issues],
            "degraded_reason": self.degraded_reason,
            "ok": self.ok,
            "container_id": self.container_id,
            "container_backend": self.container_backend,
        }


@dataclass
class TornDown:
    """What teardown actually did, in order. `steps` is the audit trail a deletion leaves behind.

    Returned rather than logged because both call sites (run deletion, retention expiry) delete
    the workspace right after — so this is the only record that the services were stopped and the
    work was committed before the directory went away.
    """

    ran: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    committed: bool = False
    removed: bool = False
    branch: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran": list(self.ran),
            "failed": list(self.failed),
            "committed": self.committed,
            "removed": self.removed,
            "branch": self.branch,
        }


def lock_key(run_id: str, name: str = "") -> str:
    return f"workflow-workspace:{name}" if name else f"workflow-run-workspace:{run_id}"


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    except PermissionError:
        alive = True
    except OSError as error:
        alive = error.errno == errno.EPERM
    return alive


class _WorkspaceLeaseFile:
    def __init__(self, path):
        self.path = path

    def acquire(self):
        try:
            descriptor = self.path.open("a+")
        except OSError as error:
            return WorkspaceLock(
                False,
                path=str(self.path),
                reason=f"could not open the lock file: {error}",
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            owner = _recorded_pid(descriptor)
            descriptor.close()
            refused = WorkspaceLock(
                False,
                path=str(self.path),
                reason="the workspace lock is held by another process",
            )
            if owner and pid_alive(owner):
                refused.held_by = owner
                refused.reason = f"another live run (pid {owner}) holds this workspace"
            return refused
        try:
            descriptor.seek(0)
            descriptor.truncate()
            descriptor.write(f"{os.getpid()}\n")
            descriptor.flush()
        except OSError:
            logger.debug("could not record the pid in %s", self.path, exc_info=True)
        return WorkspaceLock(
            True, path=str(self.path), held_by=os.getpid(), _fd=descriptor
        )


def acquire_workspace_lock(run_id: str, *, name: str = "") -> WorkspaceLock:
    from gideon.core.concurrency import lock_path

    return _WorkspaceLeaseFile(lock_path(lock_key(run_id, name))).acquire()


def _recorded_pid(fd: Any) -> int:
    try:
        fd.seek(0)
        record = (fd.read(64) or "").strip().splitlines()
        if record:
            return int(record[0])
    except (OSError, ValueError, IndexError):
        pass
    return 0


def _durable_enabled() -> bool:
    try:
        from gideon.engine.agents import runner_lifecycle

        enabled = runner_lifecycle.durable_sessions_enabled()
    except Exception:
        logger.debug("durable-sessions gate unreadable; treating as off", exc_info=True)
        enabled = False
    return enabled


class _DurableSetupJob:
    def __init__(self, argv, cwd, name, step, session_env, timeout):
        self.argv, self.cwd, self.name = argv, cwd, name
        self.step, self.environment, self.timeout = step, session_env, timeout

    async def run(self):
        import asyncio
        import time

        from gideon.engine import tmux_substrate

        deadline = time.monotonic() + self.timeout
        phase = "adopt"
        status_path = output_path = None
        while True:
            if phase == "adopt":
                alive = await tmux_substrate.has_session(self.name)
                if not alive:
                    phase = "spawn"
                    continue
                if time.monotonic() >= deadline:
                    await tmux_substrate.kill_session(self.name)
                    return (
                        False,
                        f"timed out after {self.timeout}s (waiting on a surviving durable step)",
                    )
                await asyncio.sleep(_DURABLE_POLL_SECS)
                continue
            if phase == "spawn":
                marker = Path(self.cwd) / worktrees.setup_marker(self.step)
                status_path, output_path = marker.with_suffix(
                    ".rc"
                ), marker.with_suffix(".out")
                try:
                    marker.parent.mkdir(parents=True, exist_ok=True)
                    for path in (status_path, output_path):
                        path.unlink(missing_ok=True)
                except OSError:
                    return None
                from gideon.security.sandbox import PROFILE_TOOL, spawn_shim_argv

                worker = spawn_shim_argv(list(self.argv), PROFILE_TOOL)
                command = [
                    "/bin/sh",
                    "-c",
                    _DURABLE_STEP_SH,
                    "gideon-step",
                    str(status_path),
                    str(output_path),
                    *worker,
                ]
                started = await tmux_substrate.new_session(
                    self.name,
                    workspace=str(self.cwd),
                    command=command,
                    env=self.environment,
                )
                if not started:
                    return None
                phase = "observe"
                continue
            if status_path.exists():
                return await self.read_result(status_path, output_path)
            if not await tmux_substrate.has_session(self.name):
                await asyncio.sleep(_DURABLE_POLL_SECS)
                if status_path.exists():
                    return await self.read_result(status_path, output_path)
                logger.debug(
                    "durable session %s ended without a status; bare fallback",
                    self.name,
                )
                return None
            if time.monotonic() >= deadline:
                await tmux_substrate.kill_session(self.name)
                return False, f"timed out after {self.timeout}s"
            await asyncio.sleep(_DURABLE_POLL_SECS)

    async def read_result(self, status_path, output_path):
        import asyncio

        attempts = 10
        text = ""
        while attempts:
            attempts -= 1
            try:
                text = status_path.read_text(encoding="utf-8").strip()
            except OSError:
                text = ""
            if text:
                break
            await asyncio.sleep(0.05)
        code = int(text) if text and text.lstrip("-").isdigit() else 1
        try:
            detail = output_path.read_text(encoding="utf-8", errors="replace")[:2000]
        except OSError:
            detail = ""
        return (True, detail) if code == 0 else (False, f"exited {code}: {detail}")


async def _run_step_durable(
    argv: list[str],
    cwd: str | Path,
    *,
    name: str,
    step: str,
    session_env: dict[str, str],
    timeout: float,
) -> tuple[bool, str] | None:
    return await _DurableSetupJob(argv, cwd, name, step, session_env, timeout).run()


class _StepExecution:
    def __init__(self, argv, cwd, environment, timeout):
        self.argv, self.cwd = argv, cwd
        self.environment, self.timeout = environment, timeout

    async def subprocess(self):
        import asyncio

        from gideon.security.sandbox import PROFILE_TOOL, create_subprocess_limited

        try:
            process = await create_subprocess_limited(
                *self.argv,
                profile=PROFILE_TOOL,
                cwd=str(self.cwd),
                env=self.environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                streams = await asyncio.wait_for(
                    process.communicate(), timeout=self.timeout
                )
            except asyncio.TimeoutError:
                process.kill()
                return False, f"timed out after {self.timeout}s"
        except FileNotFoundError:
            return False, f"command not found: {self.argv[0]}"
        except OSError as error:
            return False, f"could not start: {error}"[:500]
        success = process.returncode == 0
        content = streams[0 if success else 1] or b""
        detail = content.decode("utf-8", "replace")[:2000]
        return (
            (True, detail)
            if success
            else (False, f"exited {process.returncode}: {detail}")
        )


async def run_step(
    command: str,
    cwd: str | Path,
    *,
    env: dict[str, str] | None = None,
    runner: Any = None,
    timeout: float = STEP_TIMEOUT_SECS,
    durable_session: str = "",
) -> tuple[bool, str]:
    if runner is not None:
        try:
            return await runner(command, str(cwd))
        except Exception as error:
            return False, f"{type(error).__name__}: {error}"[:500]
    argv = shlex.split(command or "")
    if not argv:
        return False, "empty command"
    executable = argv[0]
    available = (
        os.path.exists(executable)
        if os.path.sep in executable
        else bool(shutil.which(executable))
    )
    if not available:
        return False, f"command not found: {executable}"
    environment = {**os.environ, **(env or {})}
    if durable_session and _durable_enabled():
        session_environment = {**(env or {})}
        for name in ("PATH", "PYTHONPATH"):
            if name in environment:
                session_environment.setdefault(name, environment[name])
        try:
            result = await _run_step_durable(
                argv,
                cwd,
                name=durable_session,
                step=command,
                session_env=session_environment,
                timeout=timeout,
            )
        except Exception:
            logger.debug(
                "durable step path failed for %s; bare fallback",
                durable_session,
                exc_info=True,
            )
        else:
            if result is not None:
                return result
    return await _StepExecution(argv, cwd, environment, timeout).subprocess()


def declares_workspace(spec: dict[str, Any]) -> bool:
    return isinstance(spec, dict) and isinstance(spec.get(WORKSPACE_KEY), dict)


def resolve_spec(
    spec: dict[str, Any], *, default_mode: str = ""
) -> tuple[WorkspaceSpec, list[SpecIssue]]:
    raw = spec.get(WORKSPACE_KEY) if isinstance(spec, dict) else None
    result, issues = parse_workspace(raw)
    if isinstance(raw, dict) and str(raw.get("mode", "") or "").strip():
        return result, issues
    if default_mode:
        try:
            result.mode = Mode(str(default_mode).strip().lower())
        except ValueError:
            result.mode = Mode.SCRATCH
            issues.append(
                SpecIssue(
                    "unknown_default_mode",
                    f"workflows.workspace_default_mode is {default_mode!r}, which is not a workspace mode; using {Mode.SCRATCH.value}",
                )
            )
    return result, issues


class _WorkspacePreparation:
    def __init__(self, spec, run_id, project_id, workspace_dir, run_dir, issues):
        self.spec = spec
        self.location = dict(
            run_id=run_id,
            project_id=project_id,
            workspace_dir=workspace_dir,
            run_dir=run_dir,
        )
        self.plan = plan_provisioning(spec, issues=issues)
        self.result = Provisioned(
            mode=spec.mode, isolated=spec.isolated, issues=list(self.plan.issues)
        )

    async def prepare(self, runner, snapshot, session):
        spec, result = self.spec, self.result
        if not self.plan.ok:
            result.ok = False
            result.degraded_reason = "; ".join(
                issue.message for issue in result.issues if issue.fatal
            )
            return result
        root, result.branch, result.degraded_reason = _create_workspace(
            spec, **self.location
        )
        result.path = root
        if spec.mode is Mode.CONTAINER and root:
            await _provision_container(
                spec, result, run_id=self.location["run_id"], from_snapshot=snapshot
            )
        if result.degraded_reason:
            result.isolated = False
        if not root:
            result.ok = False
            return result
        source = self.location["workspace_dir"]
        if result.isolated and spec.preserve_patterns and source:
            transfer = worktrees.preserve(source, root, spec.preserve_patterns)
            result.preserved, result.preserve_skipped = (
                transfer.copied,
                transfer.skipped,
            )
        if spec.setup:
            await _run_setup(
                spec,
                root,
                result,
                runner=runner,
                durable_session=session if result.isolated else "",
            )
        return result


async def provision(
    spec: WorkspaceSpec,
    *,
    run_id: str,
    project_id: str = "",
    workspace_dir: str = "",
    run_dir: Path | None = None,
    issues: list[SpecIssue] | None = None,
    runner: Any = None,
    from_snapshot: str = "",
    durable_session: str = "",
) -> Provisioned:
    preparation = _WorkspacePreparation(
        spec, run_id, project_id, workspace_dir, run_dir, issues
    )
    return await preparation.prepare(runner, from_snapshot, durable_session)


async def _provision_container(
    spec: WorkspaceSpec, out: Provisioned, *, run_id: str, from_snapshot: str = ""
) -> None:
    from gideon.automation.workflows.container_env import detect_backend, parse_manifest

    manifest, issues = parse_manifest(spec.container or None)
    fatal = [issue for issue in issues if issue.fatal]
    if fatal or not manifest.declared:
        out.degraded_reason = (
            "; ".join(issue.message for issue in fatal)
            or "container mode declared with no environment manifest; using an isolated scratch dir"
        )
        return
    backend = detect_backend()
    if backend is None:
        out.degraded_reason = "no container backend available (docker, nerdctl, or Apple's container CLI); using an isolated scratch dir"
        return
    result = await backend.provision(
        manifest,
        workspace_dir=out.path,
        run_id=run_id,
        from_snapshot=from_snapshot,
        context_dir=out.path,
    )
    if result.ok:
        out.container_id, out.container_backend = result.value, backend.name
    else:
        out.degraded_reason = (
            f"container provisioning failed ({backend.name}): {result.reason}"
        )


async def snapshot_workspace(run: Any, *, tag_suffix: str) -> str:
    state = workspace_state(run)
    identity = str(state.get("container_id", "") or "")
    executable = str(state.get("container_backend", "") or "")
    if identity and executable:
        from gideon.automation.workflows.container_env import CliContainerBackend

        backend = CliContainerBackend(executable)
        if backend.can_snapshot and backend.available():
            run_id = str(getattr(run, "id", "") or "")
            result = await backend.snapshot(
                identity, tag=f"gideon/run-{run_id}:{tag_suffix}"
            )
            if result.ok:
                return result.value
            logger.warning(
                "run %s: workspace snapshot failed: %s", run_id, result.reason
            )
    return ""


def _create_workspace(
    spec: WorkspaceSpec,
    *,
    run_id: str,
    project_id: str,
    workspace_dir: str,
    run_dir: Path | None,
) -> tuple[str, str, str]:
    if spec.mode is Mode.IN_PLACE:
        return workspace_dir, "", ""
    if spec.mode is Mode.WORKTREE:
        return _create_worktree(
            run_id, project_id=project_id, workspace_dir=workspace_dir
        )
    return _scratch_dir(run_id, spec.name, run_dir), "", ""


def _create_worktree(
    run_id: str, *, project_id: str, workspace_dir: str
) -> tuple[str, str, str]:
    from gideon.automation.loop import worktree as loop_worktree

    requirements = (
        (lambda: bool(workspace_dir), "no workspace is bound to this run's project"),
        (loop_worktree.git_available, "git is not on PATH; using a scratch dir"),
        (
            lambda: loop_worktree.is_git_repo(workspace_dir),
            "the bound workspace is not a git repo; using a scratch dir",
        ),
        (
            lambda: loop_worktree.ensure_base_commit(workspace_dir),
            "the repo has no commit to branch from; using a scratch dir",
        ),
    )
    for satisfied, reason in requirements:
        if not satisfied():
            return _scratch_dir(run_id, "", None), "", reason
    target = loop_worktree.add_worktree(workspace_dir, run_id, project_id)
    if not target:
        return _scratch_dir(run_id, "", None), "", "git could not create a worktree"
    branch, previous = worktrees.run_branch(run_id), loop_worktree.branch_name(run_id)
    if previous != branch:
        code, _ = loop_worktree._git(workspace_dir, "branch", "-M", previous, branch)
        if code:
            branch = previous
    return target, branch, ""


def _scratch_dir(run_id: str, name: str, run_dir: Path | None) -> str:
    from gideon.automation.workflows import store

    if not name:
        base = store.run_dir(run_id) if run_dir is None else run_dir
        root = Path(base) / "workspace"
    else:
        safe = "".join(
            character if character.isalnum() or character in "-_." else "-"
            for character in name
        )
        root = store.workflows_dir() / "workspaces" / (safe[:64] or "named")
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        logger.warning("could not create the scratch workspace %s", root, exc_info=True)
        return ""
    return str(root)


async def _run_setup(
    spec: WorkspaceSpec,
    root: str,
    out: Provisioned,
    *,
    runner: Any = None,
    durable_session: str = "",
) -> None:
    pending, completed = worktrees.pending_setup(root, spec.setup)
    out.setup_skipped = list(completed)
    environment = worktrees.worktree_env(root)
    for command in pending:
        success, detail = await run_step(
            command,
            root,
            env=environment,
            runner=runner,
            durable_session=durable_session,
        )
        if not success:
            out.setup_failed.append(f"{command}: {detail}"[:500])
            continue
        out.setup_ran.append(command)
        worktrees.mark_setup_done(root, command)


class _WorkspaceRemoval:
    def __init__(self, run, workspace_dir, keep_open, runner):
        self.run, self.workspace_dir = run, workspace_dir
        self.keep_open, self.runner = keep_open, runner
        self.state = workspace_state(run)
        self.result = TornDown(branch=str(self.state.get("branch", "") or ""))
        self.path = str(self.state.get("path", "") or "")
        self.commands = str(self.state.get("teardown", "") or "")
        self.isolated = bool(self.state.get("isolated", False))
        self.alive = bool(self.path) and Path(self.path).is_dir()

    async def stop_workers(self):
        try:
            from gideon.automation.workflows.containers import durable_worker_name
            from gideon.engine import tmux_substrate

            await tmux_substrate.kill_session(durable_worker_name(self.run))
        except Exception:
            logger.debug("durable session kill failed at teardown", exc_info=True)
        identity = str(self.state.get("container_id", "") or "")
        executable = str(self.state.get("container_backend", "") or "")
        if identity and executable:
            from gideon.automation.workflows.container_env import CliContainerBackend

            outcome = await CliContainerBackend(executable).remove(identity)
            self.record(
                outcome.ok,
                f"remove container {identity}",
                outcome.reason if not outcome.ok else "",
            )

    def record(self, success, command, detail):
        records = self.result.ran if success else self.result.failed
        records.append(command if success else f"{command}: {detail}"[:500])

    async def execute(self):
        await self.stop_workers()
        plan = worktrees.plan_teardown(
            teardown=self.commands,
            ephemeral=self.isolated and not self.state.get("name"),
            keep_open=self.keep_open,
        )
        if self.alive:
            if self.commands:
                for command in worktrees.setup_steps(self.commands):
                    success, detail = await run_step(
                        command, self.path, runner=self.runner
                    )
                    self.record(success, command, detail)
            if plan.commits_first:
                self.result.committed = _commit_outstanding(
                    self.path,
                    self.result.branch,
                    preserved=list(self.state.get("preserved") or []),
                )
            if plan.deletes:
                self.result.removed = _remove_workspace(
                    self.path,
                    run_id=str(getattr(self.run, "id", "") or ""),
                    project_id=str(getattr(self.run, "project_id", "") or ""),
                    workspace_dir=self.workspace_dir,
                    isolated=self.isolated,
                )
        return self.result


async def teardown(
    run: Any, *, workspace_dir: str = "", keep_open: bool = False, runner: Any = None
) -> TornDown:
    return await _WorkspaceRemoval(run, workspace_dir, keep_open, runner).execute()


def _commit_outstanding(path: str, branch: str, *, preserved: list[str]) -> bool:
    from gideon.automation.loop import worktree as loop_worktree

    protected = [
        worktrees.SETUP_MARKER_DIR,
        f"{worktrees.SETUP_MARKER_DIR}/**",
        *(entry for entry in preserved if entry),
    ]
    arguments = (f":(exclude){entry}" for entry in protected)
    code, _ = loop_worktree._git(path, "add", "-A", "--", ".", *arguments)
    if code:
        return False
    identity = ("-c", "user.name=Gideon", "-c", "user.email=code@gideon.local")
    message = f"workflow run: work on {branch or 'the run branch'}"
    code, output = loop_worktree._git(path, *identity, "commit", "-q", "-m", message)
    if code == 0:
        return True
    empty = ("nothing added to commit", "nothing to commit")
    if not any(phrase in output for phrase in empty):
        logger.debug(
            "could not commit outstanding work in %s: %s", path, output.strip()[:200]
        )
    return False


def _remove_workspace(
    path: str, *, run_id: str, project_id: str, workspace_dir: str, isolated: bool
) -> bool:
    if not isolated:
        return False
    directory = Path(path)
    registered = False
    if workspace_dir and run_id:
        from gideon.automation.loop import worktree as loop_worktree

        try:
            expected = loop_worktree.worktree_path(workspace_dir, run_id, project_id)
        except ValueError:
            expected = ""
        if expected and Path(expected) == directory:
            loop_worktree.remove_worktree(workspace_dir, run_id, project_id)
            registered = True
    if not registered:
        try:
            shutil.rmtree(directory, ignore_errors=True)
        except OSError:
            logger.debug("could not remove the workspace %s", directory, exc_info=True)
    return not directory.is_dir()


def stamp_run(run: Any, provisioned: Provisioned, spec: WorkspaceSpec) -> None:
    extra = getattr(run, "extra", None)
    if extra is not None:
        block = spec.to_dict()
        block.update(provisioned.to_dict())
        block.update(teardown=spec.teardown, name=spec.name)
        extra[WORKSPACE_KEY] = block
        if provisioned.isolated and provisioned.path:
            extra[WORKTREE_PATH_KEY] = provisioned.path
        else:
            extra.pop(WORKTREE_PATH_KEY, None)


def workspace_state(run: Any) -> dict[str, Any]:
    record = getattr(run, "extra", None) or {}
    if not isinstance(record, dict):
        return {}
    declared = record.get(WORKSPACE_KEY)
    if isinstance(declared, dict):
        return declared
    path = str(record.get(WORKTREE_PATH_KEY, "") or "")
    if path:
        return {"path": path, "isolated": bool(path)}
    return {}


def inspect_run(run: Any) -> worktrees.WorktreeState:
    identity = str(getattr(run, "id", "") or "")
    recorded = workspace_state(run)
    directory = str(recorded.get("path", "") or "")
    options = {}
    if directory and Path(directory).is_dir():
        from gideon.automation.loop import worktree as loop_worktree

        status, text = loop_worktree._git(directory, "status", "--porcelain")
        options = dict(
            porcelain=text if status == 0 else "",
            preserved=list(recorded.get("preserved") or []),
        )
    return worktrees.inspect_worktree(identity, directory or "", **options)


def stamp_preserved_path(run: Any, state: worktrees.WorktreeState) -> bool:
    extra = getattr(run, "extra", None)
    if extra is None:
        return False
    path = state.preserved_workspace_path
    if not path:
        return extra.pop(PRESERVED_PATH_KEY, None) is not None
    previous = extra.get(PRESERVED_PATH_KEY)
    extra[PRESERVED_PATH_KEY] = path
    return previous != path


def reintegration(run: Any, *, workspace_dir: str = "") -> dict[str, Any]:
    current = inspect_run(run)
    conflicts = _conflicts(run, current, workspace_dir=workspace_dir)
    offer = worktrees.reintegration_offer(
        current.run_id,
        branch=current.branch,
        changed=len(current.changed),
        conflicts=conflicts,
    )
    fields = ("mode", "isolated", "name", "degraded_reason", "setup", "issues")
    declared = {
        key: value for key, value in workspace_state(run).items() if key in fields
    }
    return dict(
        run_id=current.run_id,
        workspace=current.to_dict(),
        reintegration=offer,
        declared=declared,
    )


def _conflicts(
    run: Any, state: worktrees.WorktreeState, *, workspace_dir: str
) -> list[str]:
    if not workspace_dir or not state.branch:
        return []
    from gideon.automation.loop import worktree as loop_worktree

    if not loop_worktree.is_git_repo(workspace_dir):
        return []
    status, output = loop_worktree._git(
        workspace_dir, "merge-tree", "--write-tree", "HEAD", state.branch
    )
    if status == 0:
        return []
    unique = {}
    for line in (output or "").splitlines():
        header, separator, path = line.partition("\t")
        if separator and line[:1].isdigit():
            path = path.strip()
            if path:
                unique[path] = None
    return list(unique)
