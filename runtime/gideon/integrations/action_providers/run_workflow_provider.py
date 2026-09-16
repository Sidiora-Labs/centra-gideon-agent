"""Plan trigger-origin workflow admission and persist it before supervisor handoff."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from gideon.integrations.action_providers.base import (
    ActionContext,
    ActionProvider,
    ActionResult,
)
from gideon.integrations.action_providers.command_lifecycle import ActionClock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _WorkflowEngine:
    definitions: Any
    overlap: Any
    store: Any
    dedupe: Any
    models: Any

    @classmethod
    def load(cls) -> _WorkflowEngine:
        from gideon.automation.workflows import defs, models, overlap, store
        from gideon.automation.workflows.effects import START_DEDUPE

        return cls(defs, overlap, store, START_DEDUPE, models)


@dataclass(frozen=True)
class _WorkflowStart:
    name: str
    config: dict[str, Any]
    trigger_id: str

    @property
    def caller_key(self) -> str:
        return str(self.config.get("idempotency_key", "") or "")

    def inputs(self) -> dict[str, Any]:
        return dict(self.config.get("inputs") or {})

    def persist(
        self, engine: _WorkflowEngine, spec: dict[str, Any], *, queued: bool
    ) -> Any:
        models = engine.models
        fields = dict(
            id="",
            workflow_name=self.name,
            status=models.RunStatus.DRAFT,
            spec_version=int(spec.get("version", 1) or 1),
            inputs=self.inputs(),
            mode=str(self.config.get("mode", "background") or "background"),
            project_id=str(self.config.get("project_id", "") or ""),
            origin=models.RunOrigin(
                kind=models.OriginKind.HOOK, trigger_id=self.trigger_id
            ),
            extra=engine.overlap.queued_extra() if queued else {},
        )
        run = engine.store.create(models.WorkflowRun(**fields))
        engine.store.write_spec(run.id, spec)
        if self.caller_key:
            engine.dedupe.remember(self.caller_key, run.id)
        return run


@dataclass(frozen=True)
class _OverlapPlan:
    action: Any
    active: list[Any]
    queued: list[Any]
    engine: _WorkflowEngine

    @classmethod
    def prepare(
        cls, engine: _WorkflowEngine, name: str, definition: Any
    ) -> _OverlapPlan:
        policy = _overlap_of(definition, engine.models.OverlapPolicy)
        running = [
            run for run in engine.store.active_runs() if run.workflow_name == name
        ]
        pending = engine.overlap.queued_runs(name)
        action = engine.overlap.decide(policy, active=len(running), queued=len(pending))
        return cls(action, running, pending, engine)

    def without_start(
        self, request: _WorkflowStart, clock: ActionClock
    ) -> ActionResult | None:
        Act = self.engine.overlap.OverlapAction
        if self.action == Act.SKIP:
            body = dict(
                skipped=True, reason="already running", run_id=self.active[0].id
            )
        elif request.config.get("dry_run"):
            body = dict(
                dry_run=True,
                would=self.action.value,
                would_start=request.name,
                inputs=request.inputs(),
            )
        elif self.action == Act.DROP:
            cap = self.engine.overlap.MAX_QUEUE_DEPTH
            head = self.queued[0].id
            logger.warning(
                "run-workflow: dropped a queued start for %s — the queue is already %d deep (max %d); the pending run is %s",
                request.name,
                len(self.queued),
                cap,
                head,
            )
            body = dict(
                dropped=True,
                reason="queue_full",
                queue_depth=len(self.queued),
                max_queue_depth=cap,
                queued_run_id=head,
            )
        else:
            if self.action not in (Act.QUEUE, Act.START, Act.CANCEL_THEN_START):
                raise AssertionError(
                    f"run-workflow has no branch for OverlapAction.{self.action.name}"
                )
            return None
        return clock.result(True, outcome="skip", stdout=json.dumps(body))

    async def commit(
        self, request: _WorkflowStart, spec: dict[str, Any], clock: ActionClock
    ) -> ActionResult:
        Act = self.engine.overlap.OverlapAction
        waiting = self.action == Act.QUEUE
        run = request.persist(self.engine, spec, queued=waiting)
        if waiting:
            body = dict(
                run_id=run.id,
                workflow=request.name,
                queued=True,
                started=False,
                behind=[previous.id for previous in self.active],
            )
            return clock.result(True, outcome="queued", stdout=json.dumps(body))
        if self.action == Act.CANCEL_THEN_START:
            for previous in self.active:
                self.engine.store.request_cancel(previous.id)
        if not await _launch(run, spec):
            return ActionResult(
                False,
                error="no workflow supervisor available to start the run",
                stderr=f"run {run.id} was created but not started",
                stdout=json.dumps(dict(run_id=run.id, started=False)),
            )
        return clock.result(
            True,
            outcome="launched",
            stdout=json.dumps(dict(run_id=run.id, workflow=request.name, started=True)),
        )


class RunWorkflowActionProvider(ActionProvider):
    @property
    def name(self) -> str:
        return "run-workflow"

    @property
    def display_name(self) -> str:
        return "Run workflow"

    @property
    def supports_dry_run(self) -> bool:
        return True

    async def execute(
        self, action_config: dict[str, Any], ctx: ActionContext, timeout: int = 30
    ) -> ActionResult:
        clock = ActionClock()
        config = action_config or {}
        name = str(config.get("workflow", "") or "").strip()
        if not name:
            return ActionResult(
                False,
                error="run-workflow requires a `workflow` name",
                stderr="no workflow named in the action config",
            )
        try:
            engine = _WorkflowEngine.load()
        except Exception as error:
            return ActionResult(
                False,
                error=f"workflow engine unavailable: {error}",
                stderr="could not import the v2 workflow engine",
            )
        request = _WorkflowStart(name, config, str(getattr(ctx, "context", "") or ""))
        if request.caller_key:
            previous = engine.dedupe.lookup(request.caller_key)
            if previous:
                return clock.result(
                    True,
                    outcome="launched",
                    stdout=json.dumps(dict(run_id=previous, deduped=True)),
                )
        definition = await _load_def(engine.definitions, name)
        if definition is None:
            return ActionResult(
                False,
                error=f"unknown workflow {name!r}",
                stderr="no workflow definition by that name is registered",
            )
        spec = _spec_of(definition)
        if not isinstance(spec, dict) or not spec.get("root"):
            return ActionResult(
                False,
                error=f"workflow {name!r} has no usable spec",
                stderr="the definition carries no root node",
            )
        plan = _OverlapPlan.prepare(engine, name, definition)
        immediate = plan.without_start(request, clock)
        if immediate is not None:
            return immediate
        return await plan.commit(request, spec, clock)


async def _load_def(defs_mod: Any, name: str) -> Any | None:
    candidates = iter(defs_mod.list_providers())
    for identifier in candidates:
        source = defs_mod.get_provider(identifier)
        if source is None:
            continue
        try:
            definition = await source.get_def(name)
        except Exception:
            logger.debug("workflow def provider %s failed on %s", identifier, name)
        else:
            if definition is not None:
                return definition
    return None


def _spec_of(definition: Any) -> dict[str, Any]:
    projection = definition
    if not isinstance(projection, dict):
        convert = getattr(projection, "to_dict", None)
        projection = convert() if callable(convert) else None
    return projection if isinstance(projection, dict) else {}


def _overlap_of(definition: Any, policy_cls: Any) -> Any:
    declared = getattr(definition, "on_overlap", None)
    if declared is not None:
        return declared
    value = (
        definition.get("on_overlap", "skip") if isinstance(definition, dict) else "skip"
    )
    try:
        return policy_cls(str(value or "skip"))
    except ValueError:
        return policy_cls.SKIP


async def _launch(run: Any, spec: dict[str, Any]) -> bool:
    try:
        from gideon.integrations.action_providers.services import get_action_services

        services = get_action_services()
    except Exception:
        logger.debug("action services unavailable for run-workflow", exc_info=True)
        return False
    supervisor = getattr(services, "workflows", None) if services else None
    if supervisor is None:
        return False
    try:
        await supervisor.launch(run, spec)
    except Exception:
        logger.exception("run-workflow: supervisor refused to launch run %s", run.id)
        return False
    else:
        return True
