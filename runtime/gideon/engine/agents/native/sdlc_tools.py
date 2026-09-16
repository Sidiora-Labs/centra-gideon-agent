"""Create validated run drafts, admit launches and report stored progress to chat."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from gideon.automation.loop import files as loop_files
from gideon.integrations.tool_providers.base import ToolResult

logger = logging.getLogger(__name__)
_LOOP_CREATE_KINDS = ("goal", "general", "design", "research")
_PROJECT_KINDS = ("code", *_LOOP_CREATE_KINDS)
_KIND_LABEL = {
    "goal": "Goal Loop",
    "general": "Loop",
    "design": "Design loop",
    "research": "Research loop",
}


def _state():
    from gideon.integrations.inbox_providers import native_source

    return native_source.get_dashboard_state()


def _svc():
    from gideon.automation.triggers import nudge

    return nudge.get_instance()


def _agent_exists(body: dict) -> bool:
    from gideon.core.config.loader import AppConfig

    runtime = str(body.get("provider", ""))
    if runtime.startswith("acp:"):
        return True
    requested = str(body.get("agent", ""))
    if requested:
        try:
            return requested in AppConfig.load().agents
        except Exception:
            pass
    return True


def _warning_suffix(warnings) -> str:
    return " ⚠ " + " ".join(warnings) if warnings else ""


def _refusal(error: str, hint: str = "") -> ToolResult:
    return ToolResult(success=False, error=error, recovery_hints=[hint] if hint else [])


@dataclass
class _DraftRequest:
    kind: str
    task: str
    arguments: dict
    body: dict = field(default_factory=dict)

    def common_fields(self, cycles: int) -> dict:
        args = self.arguments
        return {
            "kind": self.kind,
            "task": self.task,
            "name": str(args.get("name") or "").strip(),
            "max_cycles": args.get("max_cycles", cycles),
            "attended": bool(args.get("attended", False)),
            "success_criteria": (
                str(args["success_criteria"]) if args.get("success_criteria") else None
            ),
        }

    async def prepare_code(self) -> None:
        from gideon.automation.loop.code_classify import _normalize_plan
        from gideon.interfaces.dashboard.handlers.loop_routes import (
            _installed_capability_catalogs,
        )

        skills, workflows = await _installed_capability_catalogs()
        allowed = [
            {str(row.get("id", "")).strip() for row in catalog if isinstance(row, dict)}
            for catalog in (skills, workflows)
        ]
        args = self.arguments
        self.body = self.common_fields(60)
        self.body.update(
            workspace_dir=str(args.get("workspace_dir", "")),
            plan=_normalize_plan(
                args.get("stage_plan") or [], allowed[0], allowed[1], None
            ),
            project_kind=str(args.get("project_kind", "greenfield")) or "greenfield",
            entry_stage=str(args.get("entry_stage", "ideation")) or "ideation",
            verify_command=str(args.get("verify_command", "")),
            test_command=str(args.get("test_command", "")),
        )

    def prepare_goal(self) -> None:
        self.body = self.common_fields(30)
        args = self.arguments
        self.body["goal_type"] = (
            str(args.get("goal_type", "open_ended")) or "open_ended"
        )
        for name in ("sub_goals", "deliverables", "scope", "rubric"):
            self.body[name] = [
                str(value) for value in args.get(name) or () if str(value).strip()
            ]
        if self.kind == "design":
            from gideon.automation.loop import kinds

            kinds.ensure_loaded()
            strategy = kinds.get_or_none("design")
            phases = (
                strategy.default_phases()
                if strategy and hasattr(strategy, "default_phases")
                else []
            )
            if phases:
                self.body.update(
                    plan=phases,
                    kind_config={"design_steps": [row["title"] for row in phases]},
                )

    def save(self) -> tuple[Any, Any] | ToolResult:
        from gideon.automation.loop import store, validation
        from gideon.interfaces.dashboard.handlers.loop_routes import (
            _build_loop_from_body,
        )

        creator = "code_project_create" if self.kind == "code" else "goal_loop_create"
        assessment = validation.validate(
            self.body, agent_exists=_agent_exists(self.body)
        )
        if not assessment.can_start:
            return _refusal(
                "; ".join(assessment.errors) or "validation failed",
                f"Fix the listed issues and call {creator} again.",
            )
        try:
            stored = store.create(_build_loop_from_body(self.body))
        except Exception as exc:
            logger.debug("%s failed", creator, exc_info=True)
            entity = "project" if self.kind == "code" else f"{self.kind} loop"
            return _refusal(f"could not create {entity}: {exc}")
        return stored, assessment

    def announcement(self, saved, warnings) -> str:
        if self.kind == "code":
            kind = self.body["project_kind"]
            text = (
                f'Created Code project draft `{saved.id}` — "{saved.name}" '
                f"({kind}, entry stage {self.body['entry_stage']}, {len(self.body['plan'])} stages). "
                f"Open: /#/code/{saved.id} . Not started yet — call code_project_start when the user gives the go."
            )
            if kind == "brownfield" and not self.body["workspace_dir"]:
                text += " It's a brownfield project with no workspace yet — ask the user to pick the codebase directory (or pass workspace_dir) before starting."
        else:
            if self.kind == "goal":
                count = len(saved.kind_config.get("sub_goals", []) or [])
                detail = f"{saved.kind_config.get('goal_type', 'open_ended')}, {count} sub-goals"
            else:
                detail = f"{len(saved.plan or [])} phases"
            text = (
                f'Created {_KIND_LABEL.get(self.kind, "Loop")} draft `{saved.id}` — "{saved.name}" '
                f"({detail}). Open: /#/loops/{saved.id} . "
                "Not started yet — call goal_loop_start when the user gives the go."
            )
        return text + _warning_suffix(warnings)

    def result(self) -> ToolResult:
        saved = self.save()
        if isinstance(saved, ToolResult):
            return saved
        loop, assessment = saved
        return ToolResult(
            success=True, output=self.announcement(loop, assessment.warnings)
        )


@dataclass
class _LaunchAdmission:
    loop: Any
    label: str
    warnings: list[str] = field(default_factory=list)

    def check(self) -> ToolResult | None:
        from gideon.automation.loop import kinds, validation
        from gideon.automation.loop.loop import ACTION_SOURCE_STATES, LoopStatus

        status = LoopStatus(self.loop.status)
        if status in ACTION_SOURCE_STATES["start"]:
            assessment = validation.validate(
                self.loop.to_dict(), agent_exists=_agent_exists(self.loop.to_dict())
            )
            if not assessment.can_start:
                return _refusal(
                    "; ".join(assessment.errors) or "validation failed",
                    "Fix the listed issues, then start again.",
                )
            self.warnings = list(assessment.warnings)
            kinds.ensure_loaded()
            strategy = kinds.get_or_none(self.loop.kind)
            blocker = getattr(strategy, "launch_blocker", None) if strategy else None
            reason = blocker(self.loop) if blocker else None
            if reason:
                return _refusal(
                    reason,
                    "A brownfield code loop needs a workspace dir; have the user pick one first.",
                )
            return None
        if status == LoopStatus.RUNNING:
            return _refusal(
                f"this {self.label} is already running.",
                "It's live — call sdlc_status with this id to report progress, no need to start it again.",
            )
        if status in {LoopStatus.COMPLETE, LoopStatus.STOPPED}:
            return _refusal(
                f"this {self.label} already finished ('{self.loop.status}') — a terminal run can't be restarted.",
                "Create a new project/loop for follow-up work.",
            )
        if status not in ACTION_SOURCE_STATES["resume"]:
            return _refusal(
                f"can't start a {self.label} in '{self.loop.status}' state."
            )
        return None


async def _launch(kind: str, lid: str, deep_path: str, label: str) -> ToolResult:
    from gideon.automation.loop import manager, store

    if not loop_files.valid_loop_id(lid):
        return _refusal(f"{label}_id is required + must be a valid id.")
    stored = store.get(lid)
    if stored is None:
        return _refusal(f"no {label} {lid!r}.")
    admission = _LaunchAdmission(stored, label)
    refusal = admission.check()
    if refusal is not None:
        return refusal
    state, service = _state(), _svc()
    if state is None or service is None:
        return _refusal("execution service unavailable (gateway not ready).")
    try:
        await manager.start(state, service, lid)
    except Exception as exc:
        logger.debug("%s start failed", label, exc_info=True)
        return _refusal(f"could not start {label}: {exc}")
    message = f"Started {label} `{lid}` — it's now running. Watch progress at {deep_path}{lid} or call sdlc_status with this id."
    return ToolResult(
        success=True, output=message + _warning_suffix(admission.warnings)
    )


async def code_project_create(a: dict) -> ToolResult:
    task = str(a.get("task", "")).strip()
    if len(task) < 12:
        return _refusal(
            "task is too vague — describe it in more detail (min 12 chars).",
            "Give a concrete SDLC task: an idea, a bugfix, a refactor, a feature.",
        )
    draft = _DraftRequest("code", task, a)
    await draft.prepare_code()
    return draft.result()


async def code_project_start(a: dict) -> ToolResult:
    identifier = str(a.get("project_id", "")).strip()
    return await _launch("code", identifier, "/#/code/", "code project")


async def goal_loop_create(a: dict) -> ToolResult:
    goal = str(a.get("goal", "")).strip()
    if len(goal) < 12:
        return _refusal(
            "goal is too vague — describe it in more detail (min 12 chars).",
            "State a concrete outcome the loop should drive toward.",
        )
    kind = str(a.get("kind", "goal")).strip().lower() or "goal"
    if kind not in _LOOP_CREATE_KINDS:
        return _refusal(
            f"kind must be one of {', '.join(_LOOP_CREATE_KINDS)} (code uses code_project_create).",
            "Pick goal for research/action, general for a generic iterative task, research for deep web research → a report, design for a design system.",
        )
    draft = _DraftRequest(kind, goal, a)
    draft.prepare_goal()
    return draft.result()


def _stored_kind(identifier: str) -> str:
    from gideon.automation.loop import store

    existing = store.get(identifier) if loop_files.valid_loop_id(identifier) else None
    return existing.kind if existing else "goal"


async def goal_loop_start(a: dict) -> ToolResult:
    identifier = str(a.get("loop_id", "")).strip()
    kind = _stored_kind(identifier)
    return await _launch(
        kind, identifier, "/#/loops/", _KIND_LABEL.get(kind, "loop").lower()
    )


async def project_create(a: dict) -> ToolResult:
    kind = str(a.get("kind", "")).strip().lower()
    if kind not in _PROJECT_KINDS:
        return _refusal(
            f"kind must be one of {', '.join(_PROJECT_KINDS)}.",
            "'code' for SDLC work in a codebase; 'goal' for research/action toward an outcome; 'research' for deep web research → a report; 'design' for a design system; 'general' for a generic iterative task.",
        )
    if kind == "code":
        return await code_project_create(a)
    forwarded = a.copy()
    if "goal" not in forwarded:
        forwarded["goal"] = str(a.get("task", "")).strip()
    return await goal_loop_create(forwarded)


async def project_start(a: dict) -> ToolResult:
    identifier = str(a.get("project_id", "")).strip()
    kind = _stored_kind(identifier)
    code = kind == "code"
    return await _launch(
        kind,
        identifier,
        "/#/code/" if code else "/#/loops/",
        "code project" if code else _KIND_LABEL.get(kind, "loop").lower(),
    )


async def project_status(a: dict) -> ToolResult:
    return await sdlc_status(dict(id=str(a.get("project_id", "")).strip()))


async def project_list(a: dict) -> ToolResult:
    from gideon.automation.loop import store

    requested = str(a.get("kind", "")).strip().lower()
    try:
        limit = max(1, int(a.get("limit", 25) or 25))
    except (ValueError, TypeError):
        limit = 25
    try:
        records = store.list_redacted(
            kind=requested if requested in _PROJECT_KINDS else ""
        )
    except Exception:
        logger.debug("project_list failed", exc_info=True)
        return _refusal("could not list projects (execution service unavailable).")
    if not records:
        return ToolResult(
            success=True, output="(no projects yet — use project_create to start one)"
        )
    selected = records[:limit]
    lines = [f"{len(selected)} project(s):"]
    for row in selected:
        lines.append(
            f"- [{row.get('kind', '?')}] {row.get('name') or row.get('id')} (id={row.get('id')}) — {row.get('status', '?')}"
        )
    return ToolResult(success=True, output="\n".join(lines))


@dataclass(frozen=True)
class _ProgressCard:
    identifier: str
    record: dict

    def progress(self) -> str:
        kind = self.record.get("kind", "goal")
        if kind == "goal":
            goals = (self.record.get("kind_config", {}) or {}).get(
                "sub_goals", []
            ) or []
            return f"{len(goals)} sub-goals" if goals else "open-ended"
        plan = self.record.get("plan", []) or []
        if not plan:
            return "no phase plan"
        finished = sum(
            value == "done"
            for value in (self.record.get("phase_status") or {}).values()
        )
        return f"{finished}/{len(plan)} {'stages' if kind == 'code' else 'phases'} done"

    def attention(self) -> str:
        status = self.record.get("status", "?")
        if status == "needs_input":
            pending = self.record.get("pending_question") or {}
            question = str(pending.get("question") or "").strip()
            reason = str(pending.get("why") or "").strip()
            if not question:
                return "⚠ Waiting on your input. "
            explanation = f" (why: {reason[:200]})" if reason else ""
            return f"⚠ Needs your input: {question[:300]}{explanation} "
        error = str(self.record.get("error_message") or "").strip()
        if error and status in {"blocked", "failed", "stagnant"}:
            return f"⚠ {status.capitalize()}: {error[:300]} "
        if error and status == "complete":
            return f"⚠ Ended early (didn't fully finish): {error[:300]} "
        return ""

    def render(self) -> str:
        kind = self.record.get("kind", "goal")
        status = self.record.get("status", "?")
        findings = self.record.get("findings", []) or []
        latest = findings[-1].get("summary", "") if findings else ""
        fragments = [
            f"{kind} `{self.identifier}` — status: {status}; {self.progress()}; {len(findings)} cycles. "
        ]
        fragments.append(self.attention())
        fragments.append(f"Latest: {latest[:200]} " if latest else "No findings yet. ")
        fragments.append(
            f"Open: /#/{'code' if kind == 'code' else 'loops'}/{self.identifier}"
        )
        return "".join(fragments)


async def sdlc_status(a: dict) -> ToolResult:
    identifier = str(a.get("id", "")).strip()
    if not identifier:
        return _refusal("id is required (a loop id).")
    from gideon.automation.loop import store

    record = (
        store.get_redacted(identifier) if loop_files.valid_loop_id(identifier) else None
    )
    if record is None:
        return _refusal(f"no loop with id {identifier!r}.")
    return ToolResult(success=True, output=_ProgressCard(identifier, record).render())
