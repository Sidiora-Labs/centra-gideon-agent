"""Workflow controller ownership, crash recovery, event delivery and retention."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path
from typing import Any

from gideon import shutdown_event
from gideon.automation.workflows import containers, overlap, store
from gideon.automation.workflows.coalescer import EventCoalescer
from gideon.automation.workflows.controller import (
    _ROOT_TO_RUN,
    EngineServices,
    RunController,
)
from gideon.automation.workflows.models import (
    TERMINAL_RUN_STATUSES,
    TERMINAL_STATES,
    InstanceState,
    Node,
    RunStatus,
    WorkflowRun,
)
from gideon.automation.workflows.tick import frontier
from gideon.core import concurrency

logger = logging.getLogger(__name__)
POLL_INTERVAL_SECS = 5
_TERMINAL_VALUES = frozenset(status.value for status in TERMINAL_RUN_STATUSES)


def registry_key(run_id: str) -> str:
    return f"workflow:{run_id}"


class _RunEventRelay:
    def __init__(self, supervisor):
        self.supervisor = supervisor

    def scoped(self, run_id):
        if self.supervisor._state is None:
            return None
        key = registry_key(run_id)

        def publish(event, payload):
            sink = self.supervisor._coalescer
            sink.publish(key, event, payload)
            if event == "workflow_run_update":
                if payload.get("status") in _TERMINAL_VALUES:
                    sink.flush(key)

        return publish

    def send(self, key, event, payload):
        state = self.supervisor._state
        if state is None:
            return
        try:
            factory = getattr(state, "workflow_sse", None)
            if callable(factory):
                factory().publish(key, event, payload)
        except Exception:
            logger.debug("workflow sse publish failed", exc_info=True)
        self.supervisor._publish_to_equivalent_loop_hub(state, key, event, payload)
        try:
            broadcast = getattr(state, "_broadcast", None)
            if callable(broadcast) and event == "workflow_run_update":
                _, separator, identity = key.partition(":")
                broadcast(
                    {
                        "type": "workflow_run_update",
                        "run_id": identity if separator else key,
                    }
                )
        except Exception:
            logger.debug("workflow ws broadcast failed", exc_info=True)

    @staticmethod
    def mirror(state, key, event, payload):
        from gideon.automation.workflows.loop_aliases import (
            base_container,
            keys_equivalent,
        )

        identity = base_container(key)
        if not identity:
            return
        destination = f"loop:{identity}"
        if not keys_equivalent(key, destination):
            return
        try:
            factory = getattr(state, "loop_sse", None)
            if callable(factory):
                hub = factory().peek(destination)
                if hub is not None:
                    hub.publish(event, payload)
        except Exception:
            logger.debug(
                "loop-hub mirror publish failed for %s", destination, exc_info=True
            )


class _RecoveryObservation:
    @staticmethod
    def durable(run):
        from gideon.engine import tmux_substrate
        from gideon.engine.agents import runner_lifecycle

        if not runner_lifecycle.durable_sessions_enabled():
            return None
        identity = containers.durable_worker_name(run)
        if tmux_substrate.has_session_sync(identity):
            return containers.Substrate(
                kind="tmux",
                alive=True,
                detail=f"durable session {identity} is still running",
            )
        directory = str((run.extra or {}).get("worktree_path", "") or "")
        if not directory:
            return None
        try:
            root = Path(directory).resolve()
        except OSError:
            return None
        for name, location in tmux_substrate.pane_paths_sync():
            try:
                pane = Path(location).resolve()
            except OSError:
                continue
            if root == pane or pane.is_relative_to(root):
                return containers.Substrate(
                    kind="tmux",
                    alive=True,
                    detail=f"durable session {name} is working in {pane}",
                )
        return None

    @staticmethod
    def workspace(run, directory):
        try:
            from gideon.automation.workflows import provisioning, worktrees

            snapshot = provisioning.inspect_run(run)
            changed = provisioning.stamp_preserved_path(run, snapshot)
            if changed:
                store.save(run)
            substrate = worktrees.substrate_for(snapshot)
        except Exception:
            logger.debug(
                "substrate inspection failed for run %s", run.id, exc_info=True
            )
            substrate = containers.Substrate(
                kind="worktree", alive=Path(directory).is_dir(), detail=directory
            )
        return substrate

    @staticmethod
    def finished(run, spec):
        try:
            tree = Node.from_dict(spec.get("root") or {})
        except ValueError:
            return None
        instances = store.read_state(run.id)
        if not instances:
            return None
        states = dict((path, instance.state) for path, instance in instances.items())
        if not all(state in TERMINAL_STATES for state in states.values()):
            return None
        result = frontier(tree, states, outputs={}, inputs=run.inputs)
        if result.complete:
            return _ROOT_TO_RUN.get(
                result.outcome or InstanceState.DONE, RunStatus.COMPLETE
            )
        return None


def _persist_resolution(run, status):
    run.status = status
    if not run.completed_at:
        run.completed_at = _now()
    store.save(run)


class _ReconciliationPass:
    def __init__(self, supervisor):
        self.supervisor = supervisor

    async def execute(self):
        owner = self.supervisor
        for identity, controller in list(owner._controllers.items()):
            if controller.run.is_terminal:
                owner._controllers.pop(identity, None)
        decided = set()
        if not owner._swept:
            decided = await owner._boot_sweep()
            owner._swept = True
        for run in store.active_runs():
            if run.id not in decided:
                await self.reconcile(run)
        await overlap.drain_all(owner)

    async def reconcile(self, run):
        owner = self.supervisor
        if store.cancel_requested(run.id):
            await owner._honor_cancel(run)
            return
        controller = owner._controllers.get(run.id)
        if controller is None:
            if (run.extra or {}).get("round_interrupted"):
                return
            await owner._adopt(run)
        elif controller.run.is_terminal:
            owner._controllers.pop(run.id, None)


class WorkflowWatchdog:
    def __init__(
        self, state: Any = None, services: EngineServices | None = None
    ) -> None:
        self._state = state
        self._services = services or EngineServices()
        self._task: asyncio.Task | None = None
        self._controllers: dict[str, RunController] = {}
        self._swept = False
        self._consec_errors = 0
        self._events = _RunEventRelay(self)
        self._coalescer = EventCoalescer(self._raw_publish)

    def start(self) -> None:
        task = self._task
        if task is not None and not task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("workflow watchdog started")

    async def stop(self) -> None:
        controllers = list(self._controllers.values())
        for controller in controllers:
            try:
                await controller.stop()
            except Exception:
                pass
        self._controllers.clear()
        try:
            self._coalescer.flush_all()
        except Exception:
            pass
        task = self._task
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None

    def controller(self, run_id: str) -> RunController | None:
        return self._controllers.get(run_id)

    def register(self, controller: RunController) -> None:
        self._controllers[controller.run.id] = controller

    def forget(self, run_id: str) -> bool:
        return self._controllers.pop(run_id, None) is not None

    async def launch(
        self, run: WorkflowRun, spec: dict[str, Any], *, depth: int = 0
    ) -> RunController:
        found = self.controller(run.id)
        if found is None:
            found = RunController(
                run, spec, services=self._services_for(run), depth=depth
            )
            self.register(found)
            await found.start()
        return found

    def _services_for(self, run: WorkflowRun) -> EngineServices:
        source = self._services
        arguments = {
            name: getattr(source, name)
            for name in ("subagents", "completion", "get_provider", "verify")
        }
        arguments.update(
            publish=self._publisher(run.id),
            attention_state=self._state,
            supervisor=self,
            model_tiers=dict(source.model_tiers),
        )
        arguments.update(
            (name, getattr(source, name))
            for name in (
                "lane_limits",
                "node_timeout_total",
                "node_timeout_stall",
                "cwd",
                "memory",
            )
        )
        return EngineServices(**arguments)

    def _publisher(self, run_id: str) -> Any:
        return self._events.scoped(run_id)

    def _raw_publish(self, key: str, event: str, payload: Any) -> None:
        self._events.send(key, event, payload)

    def _publish_to_equivalent_loop_hub(
        self, state: Any, key: str, event: str, payload: Any
    ) -> None:
        self._events.mirror(state, key, event, payload)

    async def _loop(self) -> None:
        while not shutdown_event.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                self._consec_errors += 1
                logger.warning(
                    "workflow watchdog poll failed (%d consecutive)",
                    self._consec_errors,
                    exc_info=True,
                )
            else:
                self._consec_errors = 0
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=POLL_INTERVAL_SECS
                )

    async def _poll_once(self) -> None:
        await _ReconciliationPass(self).execute()

    async def _boot_sweep(self) -> set[str]:
        return await concurrency.boot_sweep(
            "workflow-run",
            store.active_runs(),
            survived=self._is_crash_survivor,
            decide=self._sweep_one,
        )

    def _is_crash_survivor(self, run: WorkflowRun) -> bool:
        if run.status != RunStatus.RUNNING or self._controllers.get(run.id) is not None:
            return False
        from gideon.automation.workflows.round_protocol import has_round_protocol

        if has_round_protocol(store.read_spec(run.id)):
            run.extra["round_interrupted"] = True
            store.save(run)
            return False
        return True

    async def _sweep_one(self, run: WorkflowRun) -> bool:
        substrate = self._substrate_for(run)
        if substrate.isolated:
            decision = containers.sweep_decision(run, substrate)
            target = decision.status
            if target is not None and target != run.status:
                _persist_resolution(run, target)
                logger.info(
                    "workflow boot sweep: run %s → %s (%s)",
                    run.id,
                    target.value,
                    decision.reason,
                )
                return True
        return False

    def _substrate_for(self, run: WorkflowRun) -> containers.Substrate:
        surviving = self._durable_substrate(run)
        if surviving is not None:
            return surviving
        directory = str((run.extra or {}).get("worktree_path", "") or "")
        if directory:
            return _RecoveryObservation.workspace(run, directory)
        return containers.Substrate(kind="inline", alive=False)

    @staticmethod
    def _durable_substrate(run: WorkflowRun) -> containers.Substrate | None:
        return _RecoveryObservation.durable(run)

    async def _adopt(self, run: WorkflowRun) -> None:
        specification = store.read_spec(run.id)
        if specification is None:
            await self._fail(run, "run spec is missing or unreadable")
        elif not self._reap_if_finished(run, specification):
            logger.info(
                "workflow watchdog adopting run %s (%s)", run.id, run.status.value
            )
            with contextlib.suppress(Exception):
                await self.launch(run, specification)

    def _reap_if_finished(self, run: WorkflowRun, spec: dict[str, Any]) -> bool:
        terminal = _RecoveryObservation.finished(run, spec)
        if terminal is None:
            return False
        _persist_resolution(run, terminal)
        logger.info(
            "workflow watchdog reaped orphaned run %s → %s", run.id, terminal.value
        )
        return True

    async def _honor_cancel(self, run: WorkflowRun) -> None:
        if self._controllers.get(run.id) is None:
            _persist_resolution(run, RunStatus.CANCELLED)
            store.clear_cancel(run.id)
            logger.info(
                "workflow watchdog cancelled run %s (no live controller)", run.id
            )

    async def _fail(self, run: WorkflowRun, reason: str) -> None:
        run.status = RunStatus.FAILED
        run.error_message = reason
        _persist_resolution(run, RunStatus.FAILED)
        logger.warning("workflow run %s failed: %s", run.id, reason)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _RetentionSweep:
    def __init__(self, workflow_name, keep):
        records, _ = store.list_runs(workflow_name=workflow_name, limit=10_000)
        eligible = [run for run in records if run.is_terminal and not run.pinned]
        self.selected = [] if len(eligible) <= keep else eligible[keep:]

    async def remove(self, teardown_workspace):
        count = 0
        for run in self.selected:
            await teardown_workspace(run, reason="retention")
            if not _sweep_run_dir(run.id):
                continue
            store.delete(run.id)
            count += 1
        return count


async def prune_runs(workflow_name: str, *, keep: int = 100) -> int:
    from gideon.automation.workflows.service import teardown_workspace

    return await _RetentionSweep(workflow_name, keep).remove(teardown_workspace)


def _sweep_run_dir(run_id: str) -> bool:
    import shutil

    try:
        boundary = store.runs_root().resolve()
        directory = store.run_dir(run_id).resolve()
    except OSError:
        return False
    contained = directory != boundary and boundary in directory.parents
    if not contained:
        logger.error("refusing to sweep %s — outside the runs root", directory)
        return False
    if directory.exists():
        try:
            shutil.rmtree(directory)
        except OSError:
            logger.warning("could not sweep run dir %s", directory, exc_info=True)
            return False
    return True
