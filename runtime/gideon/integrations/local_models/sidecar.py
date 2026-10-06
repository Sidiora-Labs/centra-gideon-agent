"""Sidecar isolation for local-model providers (LOCAL-MODEL-MANAGER-V2 §3).

A local-model provider that wraps a crash-prone native stack (the loky segfault that
left the embedding store unsearchable is the motivating case) can declare
``provider.execution: "sidecar"`` in its manifest. Core then runs its heavy work in a
**child process with its own venv**, so a native-lib crash kills the child and raises a
typed :class:`SidecarCrashed` in the caller instead of taking the gateway down with it.

Three things live here:

:class:`SidecarRunner`
    The supervisor: a dedicated venv at ``~/.gideon/apps/{app}/venv/``, one child
    speaking the newline-JSON protocol of :mod:`._sidecar_child` (five verbs), a
    **process-generation counter**, a restart budget, and an inspectable watchdog.

:class:`SidecarInstall`
    The resumable install job (§3.2): venv → pip deps → weights, each step
    existence-checked before it does work, so an install killed halfway re-runs from
    where it died. Driven by the existing download-job registry, not a second one.

The registry of live runners (:func:`register_runner` / :func:`sweep_sidecars`)
    So the memory-pressure surface can enumerate children and one watchdog sweep can
    revive them all.

**Why generations exist.** Request ids restart at 1 in every child, so a zombie's late
reply for ``3`` could otherwise satisfy a *new* child's request ``3`` — the caller would
believe a dead process. Every id is therefore ``"<generation>:<seq>"`` and
:meth:`SidecarRunner.deliver` FENCES any frame whose generation is not the current one,
counting it in :attr:`SidecarRunner.stale_replies`. That fence is the whole reason the
counter exists.

**Why a partial line is refused.** A child killed mid-write leaves a truncated frame in
the pipe. :meth:`SidecarRunner.deliver` never sees it: the reader drops any final line
that lacks its newline, so a half-written result can never be read as a complete one.
The caller gets ``SidecarCrashed`` instead — an honest failure rather than a plausible
half-answer.

Isolation claim, stated precisely: a sidecar is a **crash and dependency** boundary (its
own process, its own venv, its own OOM-first bias via the ``tool`` ceiling profile). It
is NOT a security sandbox — the child runs with the same user and the same network
access as the gateway.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gideon.core.periodic_sweep import PeriodicSweep

logger = logging.getLogger(__name__)

_TERM_TIMEOUT = 5.0

_DEFAULT_CALL_TIMEOUT = 120.0

_LOG_TAIL_MAX = 40

_MARKER = ".gideon-sidecar.json"

_EOF = {"__eof__": True}


class SidecarCrashed(RuntimeError):
    """A sidecar child died (or hung) instead of answering.

    ``reason`` is the typed, machine-readable string the FE translates (§1 tenet 3):
    ``signal_11`` (the segfault class), ``exit_1``, ``timeout``, ``eof``,
    ``spawn_failed``, ``restart_budget_exhausted``. :attr:`typed_reason` prefixes it with
    the vocabulary's namespace (``sidecar_crashed:signal_11``) for the wire.
    """

    def __init__(self, reason: str, *, generation: int = 0, detail: str = "") -> None:
        self.reason = reason
        self.generation = generation
        self.detail = detail
        message = f"sidecar crashed (generation {generation}): {reason}"
        super().__init__(f"{message} — {detail}" if detail else message)

    @property
    def typed_reason(self) -> str:
        return f"sidecar_crashed:{self.reason}"


def sidecar_venv_dir(app: str) -> Path:
    """The dedicated venv for app *app* — ``~/.gideon/apps/{app}/venv``.

    Deliberately NOT the shared core venv: today's ``dependencies.pythonDependencies``
    land there and need a gateway restart to import. A sidecar app escapes both problems
    because its deps are only ever imported by its own child process.
    """
    from gideon.extensions.apps.manager import app_dir

    return app_dir(app) / "venv"


def venv_python(venv: Path) -> Path:
    """The interpreter inside *venv* (``Scripts\\python.exe`` on Windows)."""
    if os.name == "nt":  # pragma: no cover — POSIX is the tested path
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def _restart_max_default() -> int:
    """``local_models.sidecar_restart_max`` from config, fail-open to 3.

    Fail-open matters: a broken config must not turn into "no sidecar may ever restart",
    which would make one crash permanent.
    """
    try:
        from gideon.core.config.loader import AppConfig

        return max(0, int(AppConfig.load().local_models.sidecar_restart_max))
    except Exception:
        logger.debug("sidecar_restart_max fell back to the default", exc_info=True)
        return 3


@dataclass
class _Child:
    """One spawned generation: its process, its reader thread, its reply queue."""

    proc: subprocess.Popen
    generation: int
    output: ChildOutput | None = None
    replies: queue.Queue = field(default_factory=queue.Queue)
    reader: threading.Thread | None = None

    def is_alive(self) -> bool:
        return self.proc.poll() is None


class SidecarRunner:
    """Supervises one app's sidecar child: spawn, protocol, generations, watchdog.

    Synchronous by design (blocking pipes + a reader thread), with :meth:`acall` for the
    gateway's event loop. A sidecar's calls are already serialized — the embedding
    re-index that motivated this runs one encode at a time — so one child, one in-flight
    call, and a lock is the honest model rather than a pool that pretends otherwise.
    """

    def __init__(
        self,
        *,
        app: str,
        worker: Path | str,
        venv: Path | None = None,
        python: Path | str | None = None,
        restart_max: int | None = None,
        call_timeout: float = _DEFAULT_CALL_TIMEOUT,
        env_extra: dict[str, str] | None = None,
    ) -> None:
        self.app = app
        self.worker = Path(worker)
        self.venv = venv if venv is not None else sidecar_venv_dir(app)
        self._python_override = Path(python) if python else None
        self.restart_max = (
            _restart_max_default() if restart_max is None else int(restart_max)
        )
        self.call_timeout = float(call_timeout)
        self._env_extra = dict(env_extra or {})
        self._child: _Child | None = None
        self._generation = 0
        self._seq = 0
        self._restarts = 0
        self._consecutive_failures = 0
        self._stale_replies = 0
        self._last_stat: dict[str, Any] = {}
        self._last_reason = ""
        self._log: list[str] = []
        self._output: ChildOutput | None = None
        self._process_lock = threading.Lock()
        self._install_process: subprocess.Popen[str] | None = None
        self._cancel_requested = threading.Event()
        self._lock = threading.Lock()

    @property
    def generation(self) -> int:
        """How many children have been spawned. Every request id carries it."""
        return self._generation

    @property
    def restarts(self) -> int:
        """Total respawns after a death (observability, not the budget)."""
        return self._restarts

    @property
    def stale_replies(self) -> int:
        """Frames fenced for arriving from a superseded generation."""
        return self._stale_replies

    @property
    def last_stat(self) -> dict[str, Any]:
        """The most recent child-reported stat frame (``rss_mb``/``pid``), or ``{}``."""
        return dict(self._last_stat)

    @property
    def log_tail(self) -> list[str]:
        """The last :data:`_LOG_TAIL_MAX` non-frame lines the child wrote."""
        return self._output.lines() if self._output is not None else list(self._log)

    def last_exit(self) -> dict | None:
        child = self._child
        if child is not None and child.is_alive():
            return None
        if child is not None and child.output is not None:
            code = child.proc.poll()
            if code is not None:
                child.output.ended(code)
        return self._output.report() if self._output is not None else None

    def is_alive(self) -> bool:
        return self._child is not None and self._child.is_alive()

    def python_executable(self) -> Path:
        """The interpreter the child runs under.

        The dedicated venv when it exists, else the gateway's own interpreter — a
        provider whose deps happen to be importable in core still works, it just isn't
        dependency-isolated. Honest degradation beats refusing to run.
        """
        if self._python_override is not None:
            return self._python_override
        candidate = venv_python(self.venv)
        return candidate if candidate.is_file() else Path(sys.executable)

    def health(self) -> dict[str, Any]:
        """The runner's state as data — what the watchdog decided and why.

        A watchdog whose decision is only visible as a side effect is untestable, so
        every field the sweep keys on is readable here.
        """
        return {
            "app": self.app,
            "alive": self.is_alive(),
            "generation": self._generation,
            "pid": self._child.proc.pid if self._child is not None else 0,
            "restarts": self._restarts,
            "consecutive_failures": self._consecutive_failures,
            "restart_max": self.restart_max,
            "budget_exhausted": self._consecutive_failures > self.restart_max,
            "stale_replies": self._stale_replies,
            "last_reason": self._last_reason,
            "rss_mb": float(self._last_stat.get("rss_mb", 0.0) or 0.0),
            "venv": str(self.venv),
            "isolated": venv_python(self.venv).is_file(),
        }

    def ensure_started(self) -> int:
        """Spawn the child if it is not running. Returns the current generation.

        Raises :class:`SidecarCrashed` with ``restart_budget_exhausted`` once a child has
        died more times in a row than the budget allows — an unbounded respawn loop over
        a genuinely broken install is a busy-loop, not resilience.
        """
        with self._lock:
            return self._ensure_started_locked()

    def _ensure_started_locked(self) -> int:
        if self._child is not None and self._child.is_alive():
            return self._generation
        if self._consecutive_failures > self.restart_max:
            raise SidecarCrashed(
                "restart_budget_exhausted",
                generation=self._generation,
                detail=f"{self._consecutive_failures} consecutive failures "
                f"(sidecar_restart_max={self.restart_max})",
            )
        if self._generation > 0:
            self._restarts += 1
        self._generation += 1
        self._seq = 0
        self._child = self._spawn(self._generation)
        return self._generation

    def _spawn(self, generation: int) -> _Child:
        from gideon.security.sandbox import (
            PROFILE_TOOL,
            build_child_env,
            spawn_shim_argv,
        )

        child_harness = Path(__file__).with_name("_sidecar_child.py")
        argv = [
            str(self.python_executable()),
            str(child_harness),
            "--worker",
            str(self.worker),
        ]
        launch = spawn_shim_argv(argv, PROFILE_TOOL)
        env = build_child_env(site="model-sidecar", extra=self._env_extra)
        try:
            proc = (
                subprocess.Popen(  # noqa: S603 — argv is core-built; worker is app code
                    launch,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=env,
                    text=True,
                    bufsize=1,
                )
            )
        except OSError as exc:
            self._consecutive_failures += 1
            self._last_reason = "spawn_failed"
            raise SidecarCrashed(
                "spawn_failed", generation=generation, detail=str(exc)
            ) from exc
        output = ChildOutput(app=self.app, process="engine", pid=proc.pid, env=env)
        self._output = output
        child = _Child(proc=proc, generation=generation, output=output)
        child.reader = threading.Thread(
            target=self._read_frames,
            args=(child,),
            name=f"sidecar-{self.app}-g{generation}",
            daemon=True,
        )
        child.reader.start()
        relay(proc, output, streams=(STDERR,))
        logger.info(
            "sidecar %s started: pid=%s generation=%s python=%s",
            self.app,
            proc.pid,
            generation,
            self.python_executable(),
        )
        return child

    def _read_frames(self, child: _Child) -> None:
        """Drain the child's stdout, delivering every COMPLETE frame line.

        The newline check is load-bearing: at EOF a pipe hands back whatever partial
        bytes were written before the process died, and ``readline`` returns them as a
        line. Delivering that would be believing a half-written result.
        """
        stream = child.proc.stdout
        if stream is None:  # pragma: no cover — always a pipe here
            return
        while True:
            try:
                raw = stream.readline()
            except (ValueError, OSError):
                break
            if raw == "":
                break
            if not raw.endswith("\n"):
                if child.output is not None:
                    child.output.line(STDOUT, f"[truncated frame discarded] {raw}")
                break
            line = raw.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except ValueError:
                if child.output is not None:
                    child.output.line(STDOUT, line)
                continue
            if not isinstance(frame, dict):
                continue
            self.deliver(child.generation, frame)
        child.replies.put((child.generation, dict(_EOF)))

    def _read_logs(self, child: _Child) -> None:
        """Retain the child's stderr as the install/health log tail."""
        stream = child.proc.stderr
        if stream is None:  # pragma: no cover
            return
        for raw in stream:
            self._note_log(raw.rstrip("\n")[:200])

    def _note_log(self, line: str) -> None:
        if not line:
            return
        self._log.append(line)
        if len(self._log) > _LOG_TAIL_MAX:
            del self._log[: len(self._log) - _LOG_TAIL_MAX]

    def deliver(self, generation: int, frame: dict[str, Any]) -> bool:
        """The generation fence: accept *frame* only if *generation* is current.

        Returns False (and counts a stale reply) for a frame from a superseded child —
        the zombie-reply bug the generation counter exists to stop. A stat frame is
        recorded rather than queued: it answers no request.
        """
        if generation != self._generation:
            self._stale_replies += 1
            logger.debug(
                "sidecar %s fenced a stale frame from generation %s (current %s)",
                self.app,
                generation,
                self._generation,
            )
            return False
        if "stat" in frame and frame.get("id") is None:
            stat = frame.get("stat")
            if isinstance(stat, dict):
                self._last_stat = dict(stat)
            return True
        child = self._child
        if child is not None:
            child.replies.put((generation, frame))
        return True

    def stop(self) -> None:
        """Terminate the child (graceful, then kill). Idempotent."""
        with self._lock:
            child, self._child = self._child, None
        if child is None:
            return
        proc = child.proc
        if proc.poll() is None:
            if child.output is not None:
                child.output.stopping()
            try:
                proc.terminate()
                try:
                    proc.wait(timeout=_TERM_TIMEOUT)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=_TERM_TIMEOUT)
            except OSError:
                logger.debug("sidecar %s already gone at stop", self.app)
        for stream in (proc.stdin, proc.stdout, proc.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass
        logger.info("sidecar %s stopped (generation %s)", self.app, child.generation)

    def call(
        self,
        verb: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """Send one verb and return its result. Raises :class:`SidecarCrashed` on death.

        The child is spawned on demand, so the call AFTER a crash is what brings the next
        generation up — that is how "search recovers without a gateway restart" works
        without anyone having to notice the crash first.
        """
        deadline = self.call_timeout if timeout is None else float(timeout)
        with self._lock:
            generation = self._ensure_started_locked()
            child = self._child
            if child is None:  # pragma: no cover — _ensure_started_locked sets it
                raise SidecarCrashed("not_started", generation=generation)
            self._seq += 1
            request_id = f"{generation}:{self._seq}"
            request = {"id": request_id, "verb": verb, "payload": dict(payload or {})}
            try:
                stdin = child.proc.stdin
                if stdin is None:  # pragma: no cover
                    raise BrokenPipeError("no stdin")
                stdin.write(json.dumps(request) + "\n")
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                raise self._died(child, "eof") from None
            frame = self._await_reply(child, request_id, deadline)
        if frame.get("ok"):
            self._consecutive_failures = 0
            return frame.get("result")
        raise SidecarWorkerError(
            str(frame.get("error") or "sidecar call failed"),
            reason=str(frame.get("reason") or "worker_error"),
        )

    def _await_reply(
        self, child: _Child, request_id: str, deadline: float
    ) -> dict[str, Any]:
        """Wait for the reply to *request_id*, or raise. Called with the lock held."""
        end = time.monotonic() + deadline
        while True:
            remaining = end - time.monotonic()
            if remaining <= 0:
                raise self._died(child, "timeout")
            try:
                _, frame = child.replies.get(timeout=remaining)
            except queue.Empty:
                raise self._died(child, "timeout") from None
            if frame.get("__eof__"):
                raise self._died(child, self._exit_reason(child))
            if frame.get("id") != request_id:
                self._stale_replies += 1
                continue
            return frame

    def _exit_reason(self, child: _Child) -> str:
        """``signal_11`` / ``exit_1`` / ``eof`` from the dead child's return code."""
        try:
            code = child.proc.poll()
            if code is None:
                code = child.proc.wait(timeout=_TERM_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired):
            return "eof"
        if code is None:
            return "eof"
        return f"signal_{-code}" if code < 0 else f"exit_{code}"

    def _died(self, child: _Child, reason: str) -> SidecarCrashed:
        """Mark the child dead, tear it down, and build the typed error to raise."""
        self._consecutive_failures += 1
        self._last_reason = reason
        proc = child.proc
        if proc.poll() is None:
            try:
                proc.kill()
            except OSError:
                pass
        if self._child is child:
            self._child = None
        detail = "; ".join(self._log[-3:])
        logger.warning(
            "sidecar %s died: %s (generation %s)", self.app, reason, child.generation
        )
        return SidecarCrashed(reason, generation=child.generation, detail=detail)

    async def acall(
        self,
        verb: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> Any:
        """:meth:`call` off the event loop — what an async provider proxy awaits."""
        import asyncio

        return await asyncio.to_thread(self.call, verb, payload, timeout=timeout)

    def stat(self) -> dict[str, Any]:
        """Ask the child for a fresh ``rss_mb`` frame (also updates :attr:`last_stat`)."""
        result = self.call("stat", timeout=15.0)
        if isinstance(result, dict):
            self._last_stat = dict(result)
            return dict(result)
        return {}

    def watchdog_sweep(self) -> dict[str, Any]:
        """One supervisor decision, returned as data.

        ``noop`` (never started, or alive), ``respawned``, ``budget_exhausted`` — the
        outcome a test asserts instead of sleeping and hoping.
        """
        if self._generation == 0:
            return {"app": self.app, "action": "noop", "reason": "never_started"}
        if self.is_alive():
            return {"app": self.app, "action": "noop", "reason": "alive"}
        try:
            generation = self.ensure_started()
        except SidecarCrashed as exc:
            return {"app": self.app, "action": "budget_exhausted", "reason": exc.reason}
        return {
            "app": self.app,
            "action": "respawned",
            "generation": generation,
            "restarts": self._restarts,
        }


class SidecarWorkerError(RuntimeError):
    """The child answered with a typed failure — it is alive, the *call* failed.

    Distinct from :class:`SidecarCrashed` on purpose: a bad request or a model that
    refuses to load must not be mistaken for a process death and burn a restart.
    """

    def __init__(self, message: str, *, reason: str = "worker_error") -> None:
        self.reason = reason
        super().__init__(message)


_runners: dict[str, SidecarRunner] = {}


def register_runner(runner: SidecarRunner) -> None:
    """Track *runner* so the pressure surface and the watchdog can see it."""
    _runners[runner.app] = runner


def unregister_runner(app: str) -> None:
    """Drop and stop the named runner (app disabled / uninstalled)."""
    runner = _runners.pop(app, None)
    if runner is not None:
        runner.stop()


def get_runner(app: str) -> SidecarRunner | None:
    return _runners.get(app)


def runners() -> list[SidecarRunner]:
    """Every registered runner (registration order)."""
    return list(_runners.values())


def sweep_sidecars() -> list[dict[str, Any]]:
    """One watchdog pass over every registered runner. Returns each decision."""
    return [runner.watchdog_sweep() for runner in runners()]


def stop_all_sidecars() -> None:
    """Terminate every child. Sidecars do not survive a gateway restart (§3.1)."""
    for runner in runners():
        runner.stop()


_WATCHDOG_INTERVAL = 30


def start_sidecar_watchdog() -> threading.Thread:
    return _WATCHDOG.start()


def stop_sidecar_watchdog(timeout: float = 5.0) -> bool:
    return _WATCHDOG.stop(timeout=timeout)


def _sweep_sidecars_and_log() -> None:
    for decision in sweep_sidecars():
        if decision.get("action") != "noop":
            logger.info("sidecar watchdog: %s", decision)


_WATCHDOG = PeriodicSweep(
    "model-sidecar-watchdog", _WATCHDOG_INTERVAL, _sweep_sidecars_and_log
)


INSTALL_STEPS = ("venv", "deps", "weights")


@dataclass
class _Step:
    name: str
    status: str = "pending"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail}


class SidecarInstall:
    """One app's sidecar install: dedicated venv + pip deps + weights check.

    Resumable and idempotent by construction (§3.2). ``run()`` may be called again after
    a kill, a crash, or a success: the venv step skips when the interpreter is already
    there, the deps step skips when the receipt matches the manifest's requirement list,
    and the weights step is a disk probe. Nothing is torn down to be rebuilt.
    """

    def __init__(
        self,
        app: str,
        *,
        requirements: list[str] | None = None,
        venv: Path | None = None,
        cache_root: Path | None = None,
        model: str = "",
    ) -> None:
        self.app = app
        self.requirements = sorted(requirements or [])
        if len(self.requirements) > 50:
            raise ValueError("sidecar engine packages may contain at most 50 requirements")
        from packaging.requirements import InvalidRequirement, Requirement

        for requirement in self.requirements:
            if not isinstance(requirement, str) or not requirement.strip() or len(requirement) > 500:
                raise ValueError("sidecar engine requirements must be non-empty and bounded")
            try:
                Requirement(requirement)
            except InvalidRequirement as exc:
                raise ValueError("sidecar engine package is not a valid PEP 508 requirement") from exc
        self.venv = venv if venv is not None else sidecar_venv_dir(app)
        self.cache_root = cache_root
        self.model = model
        self.steps = [_Step(name) for name in INSTALL_STEPS]
        self.error = ""
        self.reason = ""
        self.remediation = ""
        self._log: list[str] = []
        self._process_lock = threading.Lock()
        self._install_process: subprocess.Popen[str] | None = None
        self._cancel_requested = threading.Event()

    @classmethod
    def for_app(cls, app: str) -> "SidecarInstall | None":
        """Build the install for an INSTALLED app that declares a sidecar provider.

        Returns None when the app is not installed, has no manifest, or runs
        ``execution: in-process`` — an install has nothing to mean for an app that never
        asked for a child process.
        """
        from gideon.extensions.apps.manager import APP_MANIFEST_FILENAME, app_dir
        from gideon.extensions.apps.manifest import EXECUTION_SIDECAR, AppManifest

        manifest_path = app_dir(app) / APP_MANIFEST_FILENAME
        if not manifest_path.is_file():
            return None
        try:
            manifest = AppManifest.from_json_file(manifest_path)
            if manifest.validate():
                return None
        except Exception:
            logger.debug(
                "sidecar install: unreadable manifest for %s", app, exc_info=True
            )
            return None
        provider = manifest.provider
        if provider is None or provider.execution != EXECUTION_SIDECAR:
            return None
        return cls(app, requirements=list(manifest.dependencies.sidecarDependencies))

    @property
    def installed(self) -> bool:
        """Whether the venv exists AND its deps receipt matches the manifest."""
        return venv_python(self.venv).is_file() and self._receipt_matches()

    @property
    def managed(self) -> bool:
        """Whether CORE created this venv (marker present) — the delete gate."""
        return (self.venv / _MARKER).is_file()

    @property
    def log_tail(self) -> list[str]:
        return list(self._log)

    def status(self) -> dict[str, Any]:
        """The rich poll shape (§3.2): what happened, and what to do about it."""
        return {
            "provider": self.app,
            "installed": self.installed,
            "managed": self.managed,
            "install_dir": str(self.venv),
            "steps": [s.to_dict() for s in self.steps],
            "log_tail": self.log_tail,
            "error": self.error,
            "reason": self.reason,
            "remediation": self.remediation,
        }

    def run(self) -> bool:
        """Run every step, skipping the already-satisfied ones. True if all succeeded."""
        for step in self.steps:
            if not self.run_one(step.name):
                return False
        return True

    def run_one(self, name: str) -> bool:
        """Run ONE step by name. False on failure (with ``error``/``remediation`` set).

        Exposed per-step so the job runner can publish a progress frame between steps: an
        install whose only signal was "still running" for twenty minutes of pip output is
        indistinguishable from a hang.
        """
        step = next((s for s in self.steps if s.name == name), None)
        if step is None:
            return False
        if self._cancel_requested.is_set():
            self._cancel_requested.clear()
        handler = getattr(self, f"_step_{step.name}")
        step.status = "running"
        try:
            step.status, step.detail = handler()
        except (
            Exception
        ) as exc:  # noqa: BLE001 — a step failure is reported, not raised
            from gideon.operations._installer import NoInstallerError

            step.status = "error"
            detail = exc.problem if isinstance(exc, NoInstallerError) and exc.problem else str(exc)
            step.detail = detail[:200]
            self.error = detail[:200]
            self.reason, self.remediation = _classify_install_failure(exc, step.name)
            logger.warning("sidecar install %s: step %s failed", self.app, step.name)
            return False
        return True

    def _step_venv(self) -> tuple[str, str]:
        python = venv_python(self.venv)
        if python.is_file():
            return "skipped", "venv already present"
        self.venv.parent.mkdir(parents=True, exist_ok=True)
        self._run([sys.executable, "-m", "venv", "--without-pip", str(self.venv)], timeout=300)
        if not python.is_file():
            raise RuntimeError(f"venv creation produced no interpreter at {python}")
        (self.venv / _MARKER).write_text(
            json.dumps({"app": self.app, "created_by": "gideon"}) + "\n", "utf-8"
        )
        return "done", str(self.venv)

    def _step_deps(self) -> tuple[str, str]:
        if not self.requirements:
            return "skipped", "no sidecar engine packages declared"
        if self._receipt_matches():
            return "skipped", "requirements already installed"
        from gideon.operations._installer import env_install_argv

        self._run(
            env_install_argv(
                venv_python(self.venv),
                ["--disable-pip-version-check", "--no-input", "--", *self.requirements],
            ),
            timeout=1800,
        )
        self._receipt_path().write_text(json.dumps(self.requirements) + "\n", "utf-8")
        return "done", f"{len(self.requirements)} requirement(s)"

    def _step_weights(self) -> tuple[str, str]:
        if self.cache_root is None or not self.model:
            return "skipped", "weights are fetched by the download job"
        from gideon.integrations.local_models import layouts

        if layouts.is_downloaded(self.cache_root, self.model):
            return "done", "weights present on disk"
        return "pending", "weights not downloaded yet"

    def _receipt_path(self) -> Path:
        return self.venv / ".gideon-deps.json"

    def _receipt_matches(self) -> bool:
        try:
            recorded = json.loads(self._receipt_path().read_text("utf-8"))
        except (OSError, ValueError):
            return not self.requirements
        return sorted(str(r) for r in recorded) == self.requirements

    def _run(self, argv: list[str], *, timeout: int) -> None:
        """Run one installer command, stream bounded output, and own its process group."""
        from gideon.security.sandbox import (
            PROFILE_BUILD,
            build_child_env,
            spawn_shim_argv,
        )

        launch = spawn_shim_argv(list(argv), PROFILE_BUILD)
        proc = subprocess.Popen(  # noqa: S603 — core-built argv, no shell
            launch,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=build_child_env(site="model-sidecar-install", installer="pip"),
            start_new_session=(os.name != "nt"),
        )
        with self._process_lock:
            self._install_process = proc

        def collect() -> None:
            if proc.stdout is not None:
                for line in proc.stdout:
                    self._note(line)

        reader = threading.Thread(target=collect, name=f"sidecar-install-{self.app}-log", daemon=True)
        reader.start()
        try:
            try:
                returncode = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._stop_install_process(proc)
                raise
        finally:
            reader.join(timeout=2)
            with self._process_lock:
                if self._install_process is proc:
                    self._install_process = None
        if self._cancel_requested.is_set():
            raise InterruptedError("sidecar engine installation cancelled")
        if returncode != 0:
            tail = self._log[-1:] if self._log else []
            raise RuntimeError(
                f"{Path(argv[0]).name} exited {returncode}: "
                f"{tail[-1][:160] if tail else 'no output'}"
            )

    def _stop_install_process(self, proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(proc.pid, signal.SIGTERM)
            else:  # pragma: no cover - Windows process-group path
                proc.terminate()
            proc.wait(timeout=1.5)
        except (OSError, subprocess.TimeoutExpired):
            try:
                if os.name == "posix":
                    os.killpg(proc.pid, signal.SIGKILL)
                else:  # pragma: no cover - Windows process-group path
                    proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def cancel(self) -> None:
        """Stop the active installer process group before a job detaches."""
        self._cancel_requested.set()
        with self._process_lock:
            proc = self._install_process
        if proc is not None:
            self._stop_install_process(proc)

    def _note(self, line: str) -> None:
        line = line.rstrip()[:200]
        if not line:
            return
        self._log.append(line)
        if len(self._log) > _LOG_TAIL_MAX:
            del self._log[: len(self._log) - _LOG_TAIL_MAX]

    def delete(self) -> bool:
        """Remove the venv — only ever a CORE-created one (``managed``).

        A user-supplied venv is never deleted: core did not create it and cannot know
        what else depends on it.
        """
        import shutil

        if not self.managed:
            return False
        shutil.rmtree(self.venv, ignore_errors=True)
        for step in self.steps:
            step.status, step.detail = "pending", ""
        return not self.venv.exists()


def _classify_install_failure(exc: Exception, step: str) -> tuple[str, str]:
    """``(reason, remediation)`` for a failed install step.

    ``remediation`` is deliberately distinct from the error: the error says what broke,
    the remediation says what the user should DO about it — the one field that turns a
    dead end into a next action.
    """
    from gideon.operations._installer import NoInstallerError

    if isinstance(exc, NoInstallerError) and exc.fix:
        return "pip_failed", f"{exc.fix[0].upper()}{exc.fix[1:]}, then re-run the install."
    text = str(exc).lower()
    if isinstance(exc, subprocess.TimeoutExpired) or "timed out" in text:
        return (
            "timeout",
            "Re-run the install — it resumes from the step that timed out.",
        )
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return "disk_full", "Free disk space, then re-run the install."
    if "no space" in text or "disk full" in text:
        return "disk_full", "Free disk space, then re-run the install."
    if any(
        w in text
        for w in ("connection", "network", "resolve", "unreachable", "temporary fail")
    ):
        return "network", "Check the network connection, then re-run the install."
    if step == "deps":
        return (
            "pip_failed",
            "Read the log tail for the failing requirement, then re-run.",
        )
    if step == "venv":
        return (
            "venv_failed",
            "Check that python -m venv works, then re-run the install.",
        )
    return "install_failed", "Re-run the install; it resumes from the failed step."
from gideon.operations.child_output import ChildOutput, STDERR, STDOUT, relay
