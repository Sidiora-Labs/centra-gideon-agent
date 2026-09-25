"""Durable, explicitly requested project maintenance workflow coordination."""

import asyncio
import hashlib
import json
import time
import uuid
from pathlib import Path

from gideon.automation.workflows import service, store
from gideon.core.atomic_write import atomic_write
from gideon.core.config.loader import config_dir
from gideon.engine.tasks.hierarchy import HierarchyStore
from gideon.engine.tasks.native import NativeTaskProvider

AUDITS = (
    "structural-drift",
    "simplify",
    "module-hygiene",
    "complexity",
    "performance",
    "cognitive-load",
    "documentation",
)
STAGES = tuple(stage for audit in AUDITS for stage in (audit, "drain"))
_lock = asyncio.Lock()


def _root():
    path = config_dir() / "capabilities/platform/maintenance"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _path(identifier):
    if (
        not isinstance(identifier, str)
        or len(identifier) != 32
        or any(char not in "0123456789abcdef" for char in identifier)
    ):
        raise ValueError("Invalid maintenance run identifier")
    return _root() / (identifier + ".json")


def get(identifier):
    return json.loads(_path(identifier).read_text())


def _save(row):
    row["updated_at"] = time.time()
    atomic_write(_path(row["id"]), json.dumps(row))
    return row


def view():
    return {
        "version": 1,
        "audits": list(AUDITS),
        "runs": [
            json.loads(path.read_text()) for path in sorted(_root().glob("*.json"))
        ],
        "projects": [
            {"id": p.id, "name": p.name}
            for p in HierarchyStore().list_projects()
            if p.workspace_dir
        ],
    }


async def create(body, actor, supervisor):
    if not actor or supervisor is None:
        raise ValueError("Authenticated requester and workflow supervisor required")
    if not isinstance(body, dict) or set(body) - {
        "project_id",
        "workflow",
        "verify_command",
        "guard_command",
    }:
        raise ValueError("Unsupported maintenance configuration")
    for field in ("project_id", "verify_command", "guard_command"):
        if (
            not isinstance(body.get(field), str)
            or not body[field].strip()
            or len(body[field]) > 2000
        ):
            raise ValueError(f"{field} is required and must be bounded text")
    project = HierarchyStore().get_project(body["project_id"])
    if (
        project is None
        or not project.workspace_dir
        or not Path(project.workspace_dir).is_dir()
    ):
        raise ValueError("A bound project workspace is required")
    workflow = body.get("workflow", "code-project")
    definition = await service._raw_def(workflow)
    if definition is None:
        raise ValueError("Requested maintenance workflow is not installed")
    async with _lock:
        if any(
            row["project_id"] == project.id
            and row["status"] in {"running", "cancelling"}
            for row in view()["runs"]
        ):
            raise ValueError("Project already has active maintenance")
        hierarchy = HierarchyStore()
        task_list = next(
            (
                item
                for item in hierarchy.list_task_lists(project_id=project.id)
                if item.name == "Maintenance issues"
            ),
            None,
        ) or hierarchy.create_task_list("Maintenance issues", project_id=project.id)
        return _save(
            {
                "id": uuid.uuid4().hex,
                "project_id": project.id,
                "workspace": str(Path(project.workspace_dir).resolve()),
                "workflow": workflow,
                "definition_hash": hashlib.sha256(
                    json.dumps(definition.to_dict(), sort_keys=True).encode()
                ).hexdigest(),
                "verify_command": body["verify_command"],
                "guard_command": body["guard_command"],
                "actor": actor,
                "task_list_id": task_list.id,
                "status": "running",
                "stage": 0,
                "attempt": 0,
                "child_id": None,
                "history": [],
                "issues_before": [],
                "no_progress": 0,
                "error": None,
            }
        )


async def _issues(row):
    tasks, total = await NativeTaskProvider().list_tasks(
        task_list_id=row["task_list_id"], limit=1000
    )
    if total > 1000:
        raise ValueError("Maintenance issue list exceeds 1000 tasks")
    return sorted(
        task.id for task in tasks if task.status.value not in {"done", "cancelled"}
    )


def _key(row):
    namespace = hashlib.sha256(str(_root().resolve()).encode()).hexdigest()[:20]
    return f"maintenance:{namespace}:{row['id']}:{row['stage']}:{row['attempt']}"


def _recover(row):
    if row["child_id"]:
        return store.get(row["child_id"])
    runs, total = store.list_runs(project_id=row["project_id"], limit=1000)
    if total > 1000:
        raise ValueError(
            "Workflow recovery exceeds 1000 runs; resolve before continuing"
        )
    return next(
        (run for run in runs if run.inputs.get("maintenance_key") == _key(row)), None
    )


async def advance(identifier, supervisor):
    async with _lock:
        row = get(identifier)
        if row["status"] not in {"running", "cancelling"} or supervisor is None:
            return row
        try:
            child = _recover(row)
            if child:
                row["child_id"] = child.id
                state = child.status.value
                if state == "draft" and row["status"] == "running":
                    result = await service.start_draft(child.id, supervisor=supervisor)
                    if not result.get("ok"):
                        row.update(
                            status="failed",
                            error=str(result.get("code", "Draft recovery failed")),
                        )
                    return _save(row)
                if state not in {"complete", "failed", "cancelled", "escalated"}:
                    if row["status"] == "cancelling":
                        service.cancel_run(child.id, supervisor=supervisor)
                    return _save(row)
                row["history"].append(
                    {
                        "stage": STAGES[row["stage"]],
                        "child_id": child.id,
                        "status": state,
                    }
                )
                row["child_id"] = None
                if row["status"] == "cancelling":
                    row["status"] = "cancelled"
                    row["attempt"] += 1
                    return _save(row)
                if state != "complete":
                    row.update(status="failed", error=f"Child workflow {state}")
                    row["attempt"] += 1
                    return _save(row)
                if STAGES[row["stage"]] == "drain":
                    issues = await _issues(row)
                    row["no_progress"] = (
                        row["no_progress"] + 1 if issues == row["issues_before"] else 0
                    )
                    if issues:
                        row["attempt"] += 1
                        if row["no_progress"] >= 3:
                            row.update(
                                status="failed",
                                error="Issue drain made no progress in three runs",
                            )
                        return _save(row)
                row["stage"] += 1
                row["attempt"] = 0
                row["no_progress"] = 0
            elif row["status"] == "cancelling":
                row["status"] = "cancelled"
                return _save(row)
            while (
                row["stage"] < len(STAGES)
                and STAGES[row["stage"]] == "drain"
                and not await _issues(row)
            ):
                row["stage"] += 1
            if row["stage"] == len(STAGES):
                row["status"] = "complete"
                return _save(row)
            project = HierarchyStore().get_project(row["project_id"])
            if (
                project is None
                or str(Path(project.workspace_dir).resolve()) != row["workspace"]
                or not Path(row["workspace"]).is_dir()
            ):
                raise ValueError("Project workspace binding changed or is unavailable")
            definition = await service._raw_def(row["workflow"])
            if (
                definition is None
                or hashlib.sha256(
                    json.dumps(definition.to_dict(), sort_keys=True).encode()
                ).hexdigest()
                != row["definition_hash"]
            ):
                raise ValueError(
                    "Installed workflow definition changed; start a new maintenance sequence"
                )
            stage = STAGES[row["stage"]]
            row["issues_before"] = await _issues(row)
            _save(row)
            task = f"Perform project maintenance stage {stage}. " + (
                f"Resolve the actual nonterminal tasks in Maintenance issues list {row['task_list_id']}; do not close issues without verification."
                if stage == "drain"
                else f"Audit {stage} and record actionable findings as native tasks in Maintenance issues list {row['task_list_id']}. Documentation stage may update project documentation."
            )
            result = await service.start_run(
                name=row["workflow"],
                inputs={
                    "task": task,
                    "cwd": row["workspace"],
                    "verify_command": row["verify_command"],
                    "guard_command": row["guard_command"],
                    "maintenance_key": _key(row),
                },
                supervisor=supervisor,
                project_id=row["project_id"],
                session_key=row["actor"],
                idempotency_key=_key(row),
            )
            if not result.get("ok"):
                row.update(
                    status="failed",
                    error=str(result.get("code", "Workflow launch failed")),
                    child_id=result.get("run_id"),
                )
            else:
                row["child_id"] = result["run_id"]
            return _save(row)
        except (ValueError, OSError) as error:
            row.update(status="failed", error=str(error))
            return _save(row)


async def control(identifier, action, supervisor):
    async with _lock:
        row = get(identifier)
        if action == "cancel" and row["status"] == "running":
            row["status"] = "cancelling"
        elif action == "resume" and row["status"] in {"failed", "cancelled"}:
            child = _recover(row)
            if child and child.status.value not in {
                "complete",
                "failed",
                "cancelled",
                "escalated",
            }:
                raise ValueError("Previous child must terminate before resuming")
            row.update(
                status="running",
                error=None,
                child_id=None,
                attempt=row["attempt"] + 1,
                no_progress=0,
            )
        else:
            raise ValueError("Maintenance action does not apply to current state")
        _save(row)
    return await advance(identifier, supervisor)
