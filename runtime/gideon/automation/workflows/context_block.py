"""Bounded live-run context and current-spec echoes for mutation turns."""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)
MAX_BLOCK_CHARS = 2_000
MAX_ECHO_CHARS = 6_000
MAX_RUNS_IN_BLOCK = 5
STAGING_TOOLS = frozenset({"workflow_status", "workflow_get_def", "workflow_observe"})


class ActiveRunPresentation:
    def __init__(self, runs, statuses):
        self.runs, self.statuses = runs, statuses

    def line(self, run) -> str:
        heading = f"- {run.workflow_name} (run_id: {run.id}) — {run.status.value}"
        if run.status == self.statuses.NEEDS_INPUT:
            attention = run.attention if isinstance(run.attention, dict) else {}
            prompt = str(attention.get("prompt", "") or "")
            return (
                heading + " — waiting on you" + (": " + prompt[:160] if prompt else "")
            )
        return heading + (f" ({run.error_message[:80]})" if run.error_message else "")

    def render(self, project_id: str) -> str:
        runs = [
            run
            for run in self.runs
            if not project_id or not run.project_id or run.project_id == project_id
        ]
        priorities = {
            status.value: priority
            for priority, status in enumerate(
                (self.statuses.NEEDS_INPUT, self.statuses.PAUSED, self.statuses.RUNNING)
            )
        }
        runs.sort(key=lambda run: (priorities.get(run.status.value, 9), run.created_at))
        lines = [
            "[ACTIVE WORKFLOWS — runs in flight right now. Use workflow_status{run_id} for detail, workflow_resume{run_id, answer} to answer one that is waiting.]"
        ]
        lines.extend(map(self.line, runs[:MAX_RUNS_IN_BLOCK]))
        hidden = max(0, len(runs) - MAX_RUNS_IN_BLOCK)
        if hidden:
            lines.append(f"- …and {hidden} more (workflow_status for any run_id)")
        lines.append("[End of active workflows]")
        rendered = "\n".join(lines)
        if len(rendered) > MAX_BLOCK_CHARS:
            rendered = rendered[: MAX_BLOCK_CHARS - 30].rstrip() + "\n…truncated]"
        return rendered + "\n\n"


def active_workflows_block(*, project_id: str = "") -> str:
    try:
        from gideon.automation.workflows import store
        from gideon.automation.workflows.models import RunStatus

        runs = store.active_runs()
    except Exception:
        logger.debug(
            "active-workflows block skipped (store unavailable)", exc_info=True
        )
        return ""
    if not runs:
        return ""
    try:
        return ActiveRunPresentation(runs, RunStatus).render(project_id)
    except Exception:
        logger.debug("active-workflows block skipped (render error)", exc_info=True)
        return ""


class SpecOutline:
    def __init__(self, states):
        self.states = states
        self.lines: list[str] = []

    def visit(self, node: Any, depth: int) -> None:
        try:
            structured = isinstance(node, dict)
            kind = str(node.get("kind", "?")) if structured else "?"
            identifier = str(node.get("id", "")) if structured else ""
            prefix = "  " * depth
            label = prefix + "- " + kind
            if identifier:
                label += " #" + identifier
            if self.states and identifier and identifier in self.states:
                label += f" [{self.states[identifier]}]"
            self.lines.append(label)
            if not structured:
                return
            for child in node.get("children") or []:
                self.visit(child, depth + 1)
            body = node.get("body")
            if isinstance(body, dict):
                self.branch(prefix + "  body:", body, depth)
            for name, child in (node.get("cases") or {}).items():
                self.branch(prefix + f"  case {name}:", child, depth)
            default = node.get("default")
            if isinstance(default, dict):
                self.branch(prefix + "  default:", default, depth)
        except Exception:
            logger.debug("tree render failed", exc_info=True)

    def branch(self, heading: str, node: Any, depth: int) -> None:
        self.lines.append(heading)
        self.visit(node, depth + 2)


def render_tree(
    node: Any, *, indent: int = 0, states: dict[str, str] | None = None
) -> list[str]:
    outline = SpecOutline(states)
    outline.visit(node, indent)
    return outline.lines


class StagedSpecPresentation:
    def __init__(self, run, spec, instances):
        self.run, self.spec, self.instances = run, spec, instances

    def states(self) -> dict[str, str]:
        from gideon.automation.workflows.models import Node, walk

        result = {}
        try:
            for path, node in walk(Node.from_dict(self.spec.get("root") or {})):
                instance = self.instances.get(path)
                if node.id and instance is not None:
                    result[node.id] = instance.state.value
        except (ValueError, TypeError):
            pass
        return result

    def render(self) -> str:
        from gideon.automation.workflows import secrets

        states = self.states()
        source = json.dumps(
            secrets.strip_secrets(self.spec), indent=2, ensure_ascii=False, default=str
        )
        clipped = len(source) > MAX_ECHO_CHARS
        shown = source[:MAX_ECHO_CHARS].rstrip() if clipped else source
        header = (
            f"[WORKFLOW SPEC — run {self.run.id}, spec_version {self.run.spec_version}. This is the "
            "CURRENT state on disk. Edit THIS, not a spec you generated earlier — pass "
            f"expect_version={self.run.spec_version} so a concurrent change is caught.]"
        )
        lines = [
            header,
            "",
            "Structure:",
            *render_tree(self.spec.get("root") or {}, states=states),
            "",
            "Source:",
            "```json",
            shown,
            "```",
        ]
        if clipped:
            lines.append(
                "[Source truncated — call workflow_get_def or workflow_status for the full spec before editing a node you cannot see here.]"
            )
        return "\n".join([*lines, "[End of workflow spec]"]) + "\n\n"


def staged_spec_echo(run_id: str) -> str:
    try:
        from gideon.automation.workflows import store

        run, spec = store.get(run_id), store.read_spec(run_id)
        if run is None or not isinstance(spec, dict):
            return ""
        instances = store.read_state(run_id)
    except Exception:
        logger.debug("staged echo skipped (store unavailable)", exc_info=True)
        return ""
    try:
        return StagedSpecPresentation(run, spec, instances).render()
    except Exception:
        logger.debug("staged echo skipped (render error)", exc_info=True)
        return ""


def needs_staging(tool_name: str) -> bool:
    return tool_name in STAGING_TOOLS
