"""SDLC classifier — the Code intake analyze pass.

The planner runs one LLM call the moment a Code task is submitted. It decides
*what SDLC stage the input is already at* (which picks the stages still to run),
how hard to interrogate the user upfront, whether the project is greenfield or
brownfield, and seeds the stage plan + task breakdown. Everything it returns is a
recommendation the user can override on the Plan Review.

Pure orchestration like :mod:`gideon.loops.classify`: the LLM call is
injected as a callable, so this module has no provider/dashboard coupling and is
unit-testable. Stage-plan philosophy: a staged SDLC flow
(discovery→construction→operation) with human approval gates; a bias to a runnable
vertical slice with ruthless scoping; and a plan-then-execute loop with a
kept-honest task list and read-before-write / verify-after-edit. Each stage names
ONE objective, explicit exit criteria, and ONE deliverable.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from gideon.automation.loop.sdlc_meta import ENTRY_STAGES, PROJECT_KINDS, SDLC_STAGES

logger = logging.getLogger(__name__)

AskFn = Callable[[str], Awaitable[str]]

_INTAKE_RIGORS = frozenset({"minimal", "grill", "thorough"})
_EXECUTIONS = frozenset({"solo", "multi_agent"})


@dataclass
class CodeClassification:
    """The planner's read of an SDLC task (all fields recommendations, overridable)."""

    title: str = ""
    summary: str = ""
    classified: bool = (
        True  # False = LLM failed/garbled → bare defaults (UI should warn)
    )
    entry_stage: str = "ideation"
    entry_reason: str = ""
    project_kind: str = "greenfield"
    intake_rigor: str = "grill"
    rigor_reason: str = ""
    execution: str = "solo"
    roster: list[dict] = field(default_factory=list)
    strategy_id: str = "orchestrator"
    clarifying_questions: list[str] = field(default_factory=list)
    verify_command: str = ""
    test_command: str = ""
    success_criteria: str = ""
    stage_plan: list[dict] = field(default_factory=list)
    suggested_skill_ids: list[str] = field(default_factory=list)
    suggested_workflow_ids: list[str] = field(default_factory=list)
    marketplace_suggestions: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "summary": self.summary,
            "classified": self.classified,
            "entry_stage": self.entry_stage,
            "entry_reason": self.entry_reason,
            "project_kind": self.project_kind,
            "intake_rigor": self.intake_rigor,
            "rigor_reason": self.rigor_reason,
            "execution": self.execution,
            "roster": self.roster,
            "strategy_id": self.strategy_id,
            "clarifying_questions": self.clarifying_questions,
            "verify_command": self.verify_command,
            "test_command": self.test_command,
            "success_criteria": self.success_criteria,
            "stage_plan": self.stage_plan,
            "suggested_skill_ids": self.suggested_skill_ids,
            "suggested_workflow_ids": self.suggested_workflow_ids,
            "marketplace_suggestions": self.marketplace_suggestions,
        }


def _capability_catalog(skills: list[dict] | None, workflows: list[dict] | None) -> str:
    """Render the installed skills/workflows as a compact catalog for the prompt."""
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


def _fallback_classification() -> CodeClassification:
    """A safe, EDITABLE skeleton when the planner is unavailable/garbled. Rather
    than an empty plan (a blank Plan Review the user must build from scratch), give
    a generic implement→verify ladder they can refine. ``classified=False`` keeps
    the UI's "couldn't auto-analyze — review this" warning."""
    c = CodeClassification(classified=False)
    c.stage_plan = [
        {
            "stage": "implementation",
            "title": "Implementation",
            "objective": "Build the change described in the task.",
            "exit_criteria": [
                "The described change is implemented",
                "It builds/runs without errors",
            ],
            "deliverable": "",
            "task_list_name": "Implementation",
            "agent_name": "",
            "skill_ids": [],
            "workflow_ids": [],
            "tasks": [
                {
                    "title": "Implement the task",
                    "description": "",
                    "action_plan": [],
                    "exit_criteria": [],
                    "depends_on": [],
                }
            ],
        },
        {
            "stage": "verification",
            "title": "Verification",
            "objective": "Verify the change works as intended.",
            "exit_criteria": ["The change is tested/validated"],
            "deliverable": "",
            "task_list_name": "Verification",
            "agent_name": "",
            "skill_ids": [],
            "workflow_ids": [],
            "tasks": [
                {
                    "title": "Test and verify the change",
                    "description": "",
                    "action_plan": [],
                    "exit_criteria": [],
                    "depends_on": [],
                }
            ],
        },
    ]
    return c


async def classify(
    task: str,
    ask: AskFn,
    skills_catalog: list[dict] | None = None,
    workflows_catalog: list[dict] | None = None,
    agents_catalog: list[str] | None = None,
) -> CodeClassification:
    """Run the one-shot SDLC classifier over a task. Never raises — sane defaults.

    The defaults (ideation / greenfield / grill / solo) are the safe fallback when
    the LLM is unavailable or returns garbage: treating an unclassified task as a
    fresh idea needing a grill is the least-surprising treatment.

    ``skills_catalog`` / ``workflows_catalog`` are the INSTALLED capabilities
    (each ``{id, name, description}``); only ids present survive validation.
    ``agents_catalog`` is the list of installed agent names a stage may bind to —
    a stage's ``agent_name`` is cleared if it isn't one of them.
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
        catalog += "Installed AGENTS (bind a stage to one of these names, or leave empty for the default coder):\n"  # noqa: E501
        catalog += "".join(f"- {a}\n" for a in sorted(agent_names)) + "\n"
    from gideon.integrations.prompt_providers.runtime import render_use_case_prompt

    prompt = render_use_case_prompt("code_classify", {"catalog": catalog, "task": task})
    if not prompt:
        return _fallback_classification()
    try:
        raw = await ask(prompt)
        data = _parse_obj(raw)
    except Exception:
        logger.warning(
            "code classify failed — falling back to default plan", exc_info=True
        )
        data = None
    if not isinstance(data, dict):
        return _fallback_classification()

    c = CodeClassification()
    c.title = str(data.get("title", "")).strip()[:80]
    c.summary = str(data.get("summary", "")).strip()[:300]
    entry = str(data.get("entry_stage", "")).strip()
    if entry in ENTRY_STAGES:
        c.entry_stage = entry
    c.entry_reason = str(data.get("entry_reason", "")).strip()[:300]
    kind = str(data.get("project_kind", "")).strip()
    if kind in PROJECT_KINDS:
        c.project_kind = kind
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
    c.clarifying_questions = _clean_str_list(
        data.get("clarifying_questions"), item_cap=300, count_cap=8
    )
    c.verify_command = str(data.get("verify_command", "")).strip()[:300]
    c.test_command = str(data.get("test_command", "")).strip()[:300]
    c.success_criteria = str(data.get("success_criteria", "")).strip()[:300]
    c.stage_plan = _normalize_plan(
        data.get("stage_plan"), skill_ids, workflow_ids, agent_names or None
    )
    if not c.stage_plan:
        c.stage_plan = _fallback_classification().stage_plan
        c.classified = False
    c.suggested_skill_ids = _filter_ids(data.get("suggested_skill_ids"), skill_ids)
    c.suggested_workflow_ids = _filter_ids(
        data.get("suggested_workflow_ids"), workflow_ids
    )
    return c


def _clean_str_list(raw, *, item_cap: int, count_cap: int) -> list[str]:
    """Strip + length-cap + ORDER-STABLE dedup (case-insensitive) + count-cap a list of
    strings. The planner often repeats a criterion / sub-step verbatim or in near-dupe
    casing; without dedup those show twice in Plan Review and pad the gate-judge prompt.
    """
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        raw = []
    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        if not isinstance(x, str):
            continue
        s = x.strip()[:item_cap]
        if not s:
            continue
        key = s.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
        if len(out) >= count_cap:
            break
    return out


def _normalize_plan(
    raw,
    skill_ids: set[str],
    workflow_ids: set[str],
    agent_names: set[str] | None = None,
) -> list[dict]:
    """Coerce the planner's stage_plan into clean stage dicts.

    Each stage keeps {stage, title, objective, exit_criteria, deliverable,
    task_list_name, agent_name, skill_ids, workflow_ids}. The ``stage`` id must be
    a known SDLC stage (else the row is dropped — no inventing stages); per-stage
    capability ids are validated against the installed catalog; ``agent_name`` is
    cleared unless it's a known installed agent (when a catalog is given). A stage
    with neither a known stage id nor an objective is dropped. Capped at 12.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    seen_stages: set[str] = set()
    for p in raw:
        if not isinstance(p, dict):
            continue
        stage = str(p.get("stage", "")).strip().lower()
        objective = str(p.get("objective", "")).strip()[:400]
        if stage not in SDLC_STAGES:
            stage = ""
        if not (stage or objective):
            continue
        agent_name = str(p.get("agent_name", "")).strip()
        if agent_names is not None and agent_name and agent_name not in agent_names:
            agent_name = ""
        exit_criteria = _clean_str_list(
            p.get("exit_criteria"), item_cap=200, count_cap=8
        )
        title = str(p.get("title", "")).strip()[:80] or (stage.title() if stage else "")
        eff_key = stage or title.lower()
        if not eff_key:
            continue
        if eff_key in seen_stages:
            continue
        seen_stages.add(eff_key)
        task_list_name = str(p.get("task_list_name", "")).strip()[:60] or title
        stage_dict = {
            "stage": stage,
            "title": title,
            "objective": objective,
            "exit_criteria": exit_criteria,
            "deliverable": str(p.get("deliverable", "")).strip()[:300],
            "task_list_name": task_list_name,
            "agent_name": agent_name,
            "skill_ids": _filter_ids(p.get("skill_ids"), skill_ids),
            "workflow_ids": _filter_ids(p.get("workflow_ids"), workflow_ids),
            "tasks": _normalize_tasks(p.get("tasks")),
        }
        for k in ("min_findings", "min_dwell_secs", "metric_pass", "metric_hold"):
            if k in p and p.get(k) is not None:
                stage_dict[k] = p[k]
        _apply_tick_defaults(stage_dict, stage)
        out.append(stage_dict)
    return out[:12]


_METRIC_GATED_STAGES = {"verification", "review"}


def _apply_tick_defaults(stage_dict: dict, stage: str) -> None:
    """Attach P6 tick step-key defaults to a stage dict (in place), without clobbering
    any value the planner already supplied. Conservative + reversible: only fields the
    tick engine reads, all optional, so a plan works identically if the engine is off.
    Keyed on the real SDLC stage id (already validated against SDLC_STAGES upstream)."""
    stage_dict.setdefault("min_findings", 1)
    if (stage or "").lower() in _METRIC_GATED_STAGES:
        stage_dict.setdefault("metric_pass", 3.5)
        stage_dict.setdefault("metric_hold", 2.0)


def _normalize_tasks(raw) -> list[dict]:
    """Coerce a stage's proposed tasks into clean task dicts — the per-stage
    checklist seeded into the stage's TaskList at launch. Each task carries the
    rich planning fields the Tasks entity supports so the worker has a real plan to
    execute + validate against:

    - ``title`` / ``description`` (required title; titleless dropped)
    - ``action_plan`` — ordered concrete sub-steps [str]
    - ``exit_criteria`` — checkable done-conditions [str]
    - ``depends_on`` — indices (0-based, within THIS stage) of prerequisite tasks,
      resolved to real task ids at seed time so ready/blocked + parallel scheduling
      work. Self/out-of-range/forward-only-cycle-safe handling is done at seeding.

    Capped at 12 tasks/stage; sub-lists capped to keep the plan tight.
    """
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for t in raw:
        if isinstance(t, str):
            title = t.strip()
            if title:
                out.append(
                    {
                        "title": title[:160],
                        "description": "",
                        "action_plan": [],
                        "exit_criteria": [],
                        "depends_on": [],
                    }
                )
            continue
        if not isinstance(t, dict):
            continue
        title = str(t.get("title", "")).strip()[:160]
        if not title:
            continue
        action_plan = _clean_str_list(t.get("action_plan"), item_cap=300, count_cap=10)
        exit_criteria = _clean_str_list(
            t.get("exit_criteria"), item_cap=200, count_cap=8
        )
        raw_deps = t.get("depends_on")
        if isinstance(raw_deps, (int, float, str)):
            raw_deps = [raw_deps]
        elif not isinstance(raw_deps, list):
            raw_deps = []
        depends_on: list[int] = []
        for d in raw_deps:
            if isinstance(d, str) and d.strip().lstrip("-").isdigit():
                d = int(d.strip())
            if isinstance(d, bool) or not isinstance(d, (int, float)):
                continue
            di = int(d)
            if di >= 0 and di not in depends_on:
                depends_on.append(di)
        depends_on = depends_on[:8]
        out.append(
            {
                "title": title,
                "description": str(t.get("description", "")).strip()[:500],
                "action_plan": action_plan,
                "exit_criteria": exit_criteria,
                "depends_on": depends_on,
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


def _first_json_object(raw: str) -> str | None:
    """Extract the first COMPLETE top-level ``{...}`` object by brace-depth counting,
    honoring string literals + escapes so braces inside strings don't miscount. This
    beats a greedy ``\\{[\\s\\S]*\\}`` regex, which spans from the first ``{`` to the
    LAST ``}`` anywhere — so valid JSON followed by trailing prose that contains a
    brace (e.g. ``use {key: val}``) would capture the prose too and fail to parse."""
    start = raw.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return raw[start : i + 1]
    return None


def _parse_obj(raw: str) -> object:
    raw = raw or ""
    candidates = [_first_json_object(raw)]
    greedy = re.search(r"\{[\s\S]*\}", raw)
    if greedy:
        candidates.append(greedy.group())
    for candidate in candidates:
        if not candidate:
            continue
        try:
            return json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
    return None
