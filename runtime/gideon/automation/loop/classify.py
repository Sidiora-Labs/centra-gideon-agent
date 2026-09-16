"""Goal classifier — the intake analyze pass (§6).

The planner runs one LLM call the moment a goal is submitted. It decides the
*kind* of goal (which picks the stop logic), how hard to interrogate the user
upfront, whether to run solo or multi-agent (and with which roster + strategy),
and seeds the decomposition. Everything it returns is a recommendation the user
can override on the Plan Review.

Pure orchestration like :mod:`grill`: the LLM call is injected as a callable, so
this module has no provider/dashboard coupling and is unit-testable.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from gideon.automation.loop.goal_meta import GOAL_TYPES

logger = logging.getLogger(__name__)

AskFn = Callable[[str], Awaitable[str]]

_INTAKE_RIGORS = frozenset({"minimal", "grill", "thorough"})
_EXECUTIONS = frozenset({"solo", "multi_agent"})


@dataclass
class Classification:
    """The planner's read of a goal (all fields recommendations, user-overridable)."""

    title: str = ""
    goal_type: str = "open_ended"
    classified: bool = (
        True  # False = LLM failed/garbled → bare defaults (UI should warn)
    )
    intake_rigor: str = "grill"
    rigor_reason: str = ""
    execution: str = "solo"
    roster: list[dict] = field(default_factory=list)
    strategy_id: str = "orchestrator"
    strategy_reason: str = ""
    clarifying_questions: list[str] = field(default_factory=list)
    verify_command: str = ""
    success_criteria: str = ""
    sub_goals: list[str] = field(default_factory=list)
    deliverables: list[str] = field(default_factory=list)
    primary_deliverable: str = ""
    suggested_skill_ids: list[str] = field(default_factory=list)
    suggested_workflow_ids: list[str] = field(default_factory=list)
    marketplace_suggestions: list[dict] = field(default_factory=list)
    execution_plan: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "goal_type": self.goal_type,
            "classified": self.classified,
            "intake_rigor": self.intake_rigor,
            "rigor_reason": self.rigor_reason,
            "execution": self.execution,
            "roster": self.roster,
            "strategy_id": self.strategy_id,
            "strategy_reason": self.strategy_reason,
            "clarifying_questions": self.clarifying_questions,
            "verify_command": self.verify_command,
            "success_criteria": self.success_criteria,
            "sub_goals": self.sub_goals,
            "deliverables": self.deliverables,
            "primary_deliverable": self.primary_deliverable,
            "suggested_skill_ids": self.suggested_skill_ids,
            "suggested_workflow_ids": self.suggested_workflow_ids,
            "marketplace_suggestions": self.marketplace_suggestions,
            "execution_plan": self.execution_plan,
        }


def _capability_catalog(skills: list[dict] | None, workflows: list[dict] | None) -> str:
    """Render the installed skills/workflows as a compact catalog for the prompt.

    Each entry is ``- <id>: <name> — <description>`` so the LLM can rank by id.
    Returns '' when both are empty (the prompt then omits the capability step in
    spirit — the model simply has nothing to pick and returns empty lists)."""
    lines: list[str] = []
    for label, items in (("SKILLS", skills or []), ("WORKFLOWS", workflows or [])):
        usable = [
            c for c in items if isinstance(c, dict) and str(c.get("id", "")).strip()
        ]
        if not usable:
            continue
        lines.append(f"Installed {label} (id: name — description):")
        for c in usable[:60]:
            cid = str(c.get("id", "")).strip()
            name = str(c.get("name", "")).strip()
            desc = str(c.get("description", "")).strip()[:140]
            lines.append(f"- {cid}: {name}{' — ' + desc if desc else ''}")
        lines.append("")
    return "\n".join(lines)


async def classify(
    goal: str,
    ask: AskFn,
    skills_catalog: list[dict] | None = None,
    workflows_catalog: list[dict] | None = None,
    agents_catalog: list[str] | None = None,
) -> Classification:
    """Run the one-shot classifier over a goal. Never raises — returns sane defaults.

    The defaults (open_ended / grill / solo) are the safe fallback when the LLM is
    unavailable or returns garbage: an open-ended grill is the least-surprising
    treatment for an unclassified goal.

    ``skills_catalog`` / ``workflows_catalog`` are the INSTALLED capabilities
    (each ``{id, name, description}``); when provided the planner ranks the
    relevant ids into ``suggested_skill_ids`` / ``suggested_workflow_ids``. Only
    ids present in the catalog survive validation. ``agents_catalog`` is the list
    of installed agent names the planner may bind to a phase/role — a phase's
    ``agent_name`` is cleared if it isn't one of them (no inventing agents).
    """
    skill_ids = {
        str(c.get("id", "")).strip()
        for c in (skills_catalog or [])
        if isinstance(c, dict)
    }
    workflow_ids = {
        str(c.get("id", "")).strip()
        for c in (workflows_catalog or [])
        if isinstance(c, dict)
    }
    agent_names = {str(a).strip() for a in (agents_catalog or []) if str(a).strip()}
    catalog = _capability_catalog(skills_catalog, workflows_catalog)
    if agent_names:
        catalog += "Installed AGENTS (bind a phase/role to one of these names, or leave empty for the default worker):\n"  # noqa: E501
        catalog += "".join(f"- {a}\n" for a in sorted(agent_names)) + "\n"
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("goal_classify", {"catalog": catalog, "goal": goal})
    if not prompt:
        return Classification(classified=False)
    try:
        raw = await ask(prompt)
        data = _parse_obj(raw)
    except Exception:
        logger.debug("classify failed", exc_info=True)
        data = None
    if not isinstance(data, dict):
        return Classification(classified=False)

    c = Classification()
    c.title = str(data.get("title", "")).strip()[:80]
    gt = str(data.get("goal_type", "")).strip()
    if gt in GOAL_TYPES:
        c.goal_type = gt
    rigor = str(data.get("intake_rigor", "")).strip()
    if rigor in _INTAKE_RIGORS:
        c.intake_rigor = rigor
    c.rigor_reason = str(data.get("rigor_reason", "")).strip()[:300]
    execution = str(data.get("execution", "")).strip()
    if execution in _EXECUTIONS:
        c.execution = execution
    c.roster = _normalize_roster(data.get("roster"))
    if c.execution == "multi_agent" and not c.roster:
        c.execution = "solo"
    c.strategy_id = str(data.get("strategy_id", "")).strip() or "orchestrator"
    c.strategy_reason = str(data.get("strategy_reason", "")).strip()[:300]
    c.clarifying_questions = [
        str(q).strip()
        for q in (data.get("clarifying_questions") or [])
        if isinstance(q, str) and str(q).strip()
    ][:8]
    c.verify_command = str(data.get("verify_command", "")).strip()
    c.success_criteria = str(data.get("success_criteria", "")).strip()
    c.sub_goals = [
        str(s).strip()
        for s in (data.get("sub_goals") or [])
        if isinstance(s, str) and str(s).strip()
    ][:20]
    c.deliverables = [
        str(s).strip()
        for s in (data.get("deliverables") or [])
        if isinstance(s, str) and str(s).strip()
    ][:10]
    pd = str(data.get("primary_deliverable", "") or "").strip()
    c.primary_deliverable = os.path.basename(pd) if (pd and not c.deliverables) else ""
    c.suggested_skill_ids = _filter_ids(data.get("suggested_skill_ids"), skill_ids)
    c.suggested_workflow_ids = _filter_ids(
        data.get("suggested_workflow_ids"), workflow_ids
    )
    c.execution_plan = _normalize_plan(
        data.get("execution_plan"), skill_ids, workflow_ids, agent_names or None
    )
    if c.goal_type == "monitor":
        c.verify_command = ""
    if c.goal_type == "verifiable" and not c.verify_command:
        c.goal_type = "open_ended"
    return c


def _normalize_plan(
    raw,
    skill_ids: set[str],
    workflow_ids: set[str],
    agent_names: set[str] | None = None,
) -> list[dict]:
    """Coerce the planner's execution_plan into clean phase dicts.

    Each phase keeps {role, agent_name, target, min_cycles, phase_exit,
    skill_ids, workflow_ids}; per-phase capability ids are validated against the
    installed catalog (same anti-hallucination guard as the baseline), and
    ``agent_name`` is cleared unless it's a known installed agent (when a catalog
    is given) so the planner can't invent agents. A phase with neither a role nor
    a target is dropped. Capped at 12 phases."""
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for p in raw:
        if not isinstance(p, dict):
            continue
        role = str(p.get("role", "")).strip()[:60]
        target = str(p.get("target", "")).strip()[:300]
        if not (role or target):
            continue
        try:
            min_cycles = max(1, int(p.get("min_cycles", 1)))
        except (TypeError, ValueError):
            min_cycles = 1
        agent_name = str(p.get("agent_name", "")).strip()
        if agent_names is not None and agent_name and agent_name not in agent_names:
            agent_name = ""
        out.append(
            {
                "role": role,
                "agent_name": agent_name,
                "target": target,
                "min_cycles": min_cycles,
                "phase_exit": str(p.get("phase_exit", "")).strip()[:300],
                "skill_ids": _filter_ids(p.get("skill_ids"), skill_ids),
                "workflow_ids": _filter_ids(p.get("workflow_ids"), workflow_ids),
            }
        )
    return out[:12]


def _filter_ids(raw, allowed: set[str]) -> list[str]:
    """Keep only string ids present in ``allowed`` (dedup, order-stable)."""
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for v in raw:
        s = str(v).strip()
        if s and s in allowed and s not in out:
            out.append(s)
    return out[:20]


def _normalize_roster(roster) -> list[dict]:
    if not isinstance(roster, list):
        return []
    out: list[dict] = []
    for m in roster:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role", "")).strip()
        persona = str(m.get("persona", "")).strip()
        if not (role or persona):
            continue
        out.append(
            {
                "role": role,
                "persona": persona,
                "role_hint": str(m.get("role_hint", "")).strip(),
            }
        )
    return out[:5]


def _parse_obj(raw: str) -> object:
    m = re.search(r"\{[\s\S]*\}", raw or "")
    if not m:
        return None
    try:
        return json.loads(m.group())
    except (json.JSONDecodeError, ValueError):
        return None
