"""One displayed, durable start decision for a compiled chat batch."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from gideon.core.atomic_write import atomic_json_write
from gideon.security.owner_grants import GrantBook
from gideon.security.owner_only import gideon_home

PREFIX = "batch-start:"
RUN_KEY = "batch_start_consent"
_NAME = re.compile(r"[a-zA-Z0-9_-]{1,160}\Z")


def _path(name):
    if not _NAME.fullmatch(name):
        raise ValueError("invalid batch name")
    return gideon_home() / "workflow_batches" / (name + ".json")


def _content(record):
    return json.dumps(
        {
            key: record[key]
            for key in (
                "name",
                "spec",
                "inputs",
                "source",
                "session_key",
                "origin_chat_key",
                "app_scope",
                "digest",
                "asked_at",
                "tasks",
                "may_change",
                "private_receipt",
            )
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _start_content(record):
    return json.dumps(
        {"content": _content(record), "decision": record.get("decision")},
        sort_keys=True,
        separators=(",", ":"),
    )


def _save(record):
    path = _path(record["name"])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json_write(path, record)
    path.chmod(0o600)


def read(name):
    try:
        record = json.loads(_path(name).read_text())
        if not isinstance(record, dict) or record.get("name") != name:
            return None
        if not GrantBook("workflow_batch_waits").holds(name, _content(record)):
            return None
        return record
    except (OSError, ValueError, KeyError, TypeError):
        return None


def state_of(name):
    record = read(name)
    if record is None:
        return None
    return {
        key: record.get(key)
        for key in (
            "name",
            "status",
            "run_id",
            "error",
            "tasks",
            "may_change",
            "asked_at",
            "digest",
        )
    } | {"batch": name, "approval": PREFIX + name}


def _tasks(spec):
    result = []

    def walk(node):
        if not isinstance(node, dict):
            return
        if node.get("kind") == "stage":
            cfg = node.get("config") or {}
            result.append(
                {
                    "node_id": str(node.get("id") or ""),
                    "label": str(node.get("label") or node.get("id") or "Task"),
                    "agent": str(cfg.get("agent") or ""),
                    "mutating": cfg.get("capability") == "mutating",
                    "prompt": str(cfg.get("prompt") or ""),
                }
            )
        for child in node.get("children") or []:
            walk(child)

    walk(spec.get("root"))
    return result


@dataclass(frozen=True)
class BatchAdmission:
    name: str
    content: str


@dataclass(frozen=True)
class BatchStartApproval:
    run_id: str
    node_id: str
    digest: str
    task_digest: str
    agent: str
    cwd: str
    nonce: str


def bind_run(run, spec, admission):
    if not isinstance(admission, BatchAdmission):
        return False
    record = read(admission.name)
    if (
        record is None
        or not GrantBook("workflow_batch_starts").holds(
            admission.name, _start_content(record)
        )
        or _content(record) != admission.content
        or record["spec"] != spec
    ):
        return False
    run.extra = {
        **run.extra,
        RUN_KEY: {
            "name": admission.name,
            "digest": record["digest"],
            "decision": record.get("decision"),
        },
    }
    return True


def issue_for_stage(run_id, node_id, *, task, agent, cwd):
    from gideon.automation.workflows import automation_versions, store
    from gideon.security.durable_work import verified_run_origin

    run = store.get(run_id)
    if run is None or RUN_KEY not in run.extra:
        return None
    seal = run.extra[RUN_KEY]
    record = read(seal.get("name", ""))
    spec = store.read_spec(run_id)
    if (
        record is None
        or not isinstance(spec, dict)
        or verified_run_origin(run) is None
        or not GrantBook("workflow_batch_starts").holds(
            record["name"], _start_content(record)
        )
        or automation_versions.digest(spec) != seal.get("digest")
        or not any(row["node_id"] == node_id for row in _tasks(spec))
    ):
        raise PermissionError("batch start consent no longer matches this task")
    return BatchStartApproval(
        run_id,
        node_id,
        seal["digest"],
        hashlib.sha256(task.encode()).hexdigest(),
        agent,
        cwd,
        uuid.uuid4().hex,
    )


def validate_start_approval(permit, info):
    from gideon.automation.workflows import ownership

    if not isinstance(permit, BatchStartApproval):
        return False
    if (
        info.parent_run != "workflow:" + permit.run_id
        or info.parent_session_key != ownership.owned_key(permit.run_id, permit.node_id)
        or info.agent != permit.agent
        or info.cwd != permit.cwd
        or hashlib.sha256(str(info._raw_task or info.task).encode()).hexdigest()
        != permit.task_digest
    ):
        return False
    fresh = issue_for_stage(
        permit.run_id,
        permit.node_id,
        task=str(info._raw_task or info.task),
        agent=info.agent,
        cwd=info.cwd,
    )
    return fresh is not None and fresh.digest == permit.digest


async def start(state, supervisor, *, spec, inputs, context):
    from gideon.automation.workflows import (
        automation_versions,
        definition_check,
        service,
    )
    from gideon.security.durable_work import persist_accepted_origin

    checked = await definition_check.run_once_def(spec)
    if not checked.get("ok"):
        return checked
    spec = checked["definition"]
    source = persist_accepted_origin(context.get("accepted_origin"))
    if source is None or not context.get("session_key"):
        return service._service_failure(
            "WF_BATCH_SOURCE_UNAVAILABLE",
            "An attended batch requires authenticated original chat work.",
        )
    from gideon.security.durable_work import accepted_origin_values

    values = accepted_origin_values(context.get("accepted_origin"))
    private_receipt = None
    if values and values.get("memory_mode") in {"temporary", "incognito"}:
        if not _private_envelope(values):
            return service._service_failure(
                "WF_BATCH_SOURCE_UNAVAILABLE",
                "The private chat model has not been authenticated.",
            )
        from gideon.automation.workflows import private_work
        from gideon.security.session_credentials import current_work

        receipt = await private_work.ensure(supervisor, current_work())
        private_receipt = private_work.wait_receipt_record(receipt)
    name = str(spec.get("name") or "")
    tasks = _tasks(spec)
    record = {
        "name": name,
        "spec": spec,
        "inputs": dict(inputs or {}),
        "source": source,
        "digest": automation_versions.digest(spec),
        "status": "awaiting_approval",
        "asked_at": time.time(),
        "session_key": context["session_key"],
        "origin_chat_key": context.get("origin_chat_key") or context["session_key"],
        "private_receipt": private_receipt,
        "app_scope": __import__(
            "gideon.extensions.apps.app_work", fromlist=["stamp"]
        ).stamp({}, context.get("app_work")),
        "tasks": len(tasks),
        "may_change": [row["label"] for row in tasks if row["mutating"]],
    }
    previous = read(name)
    if previous is not None:
        if (
            previous["session_key"] != context["session_key"]
            or previous["origin_chat_key"] != record["origin_chat_key"]
        ):
            return service._service_failure(
                "WF_BATCH_NAME_EXISTS", "This batch name is already in use."
            )
        return service._ok(**state_of(name))
    GrantBook("workflow_batch_waits").give(name, _content(record))
    _save(record)
    grant = _read_start_grant(state, context) if not record["may_change"] else None
    if grant is not None:
        record = {**record, "decision": {"by": grant, "allowed_at": time.time()}}
        _save(record)
        GrantBook("workflow_batch_starts").give(name, _start_content(record))
        from gideon.automation.workflows.models import OriginKind

        result = await service.start_run(
            name=name,
            run_once_definition=spec,
            inputs=record["inputs"],
            origin_kind=OriginKind.SUBAGENT_TOOL,
            supervisor=supervisor,
            idempotency_key="batch:" + name,
            batch_admission=BatchAdmission(name, _content(record)),
            **context,
        )
        _save(
            {
                **record,
                "status": "running" if result.get("ok") else "not_started",
                "run_id": result.get("run_id"),
                "error": (
                    ""
                    if result.get("ok")
                    else result.get("message", "The read-only batch could not start.")
                ),
                "by": grant,
            }
        )
        return result | {"batch": name}
    _schedule(state, supervisor, record, context)
    return service._ok(**state_of(name))


def _schedule(state, supervisor, record, context):
    tasks = getattr(state, "_workflow_batch_tasks", None)
    if tasks is None:
        tasks = state._workflow_batch_tasks = {}
    if record["name"] in tasks:
        return
    task = asyncio.create_task(_wait(state, supervisor, record, context))
    tasks[record["name"]] = task
    task.add_done_callback(lambda _: tasks.pop(record["name"], None))


async def _wait(state, supervisor, record, context):
    from gideon.automation.workflows import service
    from gideon.automation.workflows.models import OriginKind

    name = record["name"]
    rows = _tasks(record["spec"])
    purpose = f"Starts {len(rows)} tasks together; {len(record['may_change'])} may change things. Allow starts these displayed tasks; Deny starts none."
    said = (
        "\n".join(
            f"{i + 1}. {row['label']} ({row['agent']}): {'may change things' if row['mutating'] else 'only reads'}.\n{row['prompt']}"
            for i, row in enumerate(rows)
        )
        + "\nDisplayed batch digest: "
        + record["digest"]
    )

    def decision(approved, by):
        current = read(name)
        if current is None or current["status"] != "awaiting_approval":
            raise ValueError("batch no longer waits")
        if approved:
            current = {
                **current,
                "decision": {
                    "by": by.label,
                    "allowed_at": time.time(),
                    "approval": PREFIX + name,
                },
            }
            GrantBook("workflow_batch_starts").give(
                name, _start_content(current), principal=by.label
            )
        _save(
            {
                **current,
                "status": "starting" if approved else "not_started",
                "error": "" if approved else "The owner denied this batch.",
            }
        )

    try:
        from gideon.security.approval_grants import approval_window_secs

        remaining = max(
            0.0, approval_window_secs() - (time.time() - record["asked_at"])
        )
        allowed = await state.request_approval(
            PREFIX + name,
            "workflow_batch",
            "subagent_run",
            tool_input=said,
            tool_purpose=purpose,
            session=record["origin_chat_key"].removeprefix("dashboard:"),
            owner_only=bool(record["may_change"]),
            on_decision=decision,
            approval_timeout_secs=remaining,
        )
        current = read(name)
        if current is None:
            return
        if not allowed:
            if current["status"] == "awaiting_approval":
                _save(
                    {
                        **current,
                        "status": "not_started",
                        "error": "The batch ended without an owner Allow.",
                    }
                )
            return
        result = await service.start_run(
            name=name,
            run_once_definition=current["spec"],
            inputs=current["inputs"],
            origin_kind=OriginKind.SUBAGENT_TOOL,
            supervisor=supervisor,
            idempotency_key="batch:" + name,
            batch_admission=BatchAdmission(name, _content(current)),
            **context,
        )
        _save(
            {
                **current,
                "status": "running" if result.get("ok") else "not_started",
                "run_id": result.get("run_id"),
                "error": (
                    ""
                    if result.get("ok")
                    else result.get("message", "The allowed batch could not start.")
                ),
            }
        )
    except asyncio.CancelledError:
        # Shutdown keeps an unanswered durable record; privacy retirement erases it first.
        raise
    except Exception as error:
        current = read(name)
        if current is not None:
            _save(
                {
                    **current,
                    "status": "not_started",
                    "error": str(error) or "The owner approval window expired.",
                }
            )


async def restore(state, supervisor):
    from gideon.security.approval_grants import approval_window_secs

    directory = gideon_home() / "workflow_batches"
    for path in directory.glob("*.json"):
        record = read(path.stem)
        if record is None or record.get("status") not in {
            "awaiting_approval",
            "starting",
        }:
            continue
        from gideon.automation.workflows import store

        existing, _ = store.list_runs(workflow_name=record["name"], limit=100)
        own = next(
            (
                run
                for run in existing
                if run.extra.get(RUN_KEY, {}).get("name") == record["name"]
            ),
            None,
        )
        if own is not None:
            _save({**record, "status": "running", "run_id": own.id})
            continue
        context = await _restore_context(record, state, supervisor)
        if context is None or time.time() - record["asked_at"] > approval_window_secs():
            _save(
                {
                    **record,
                    "status": "not_started",
                    "error": "Its original chat ended or the owner approval window expired.",
                }
            )
            continue
        # A restart never treats a recorded yes as a new answer; existing native run dedupe protects already launched work.
        _save({**record, "status": "awaiting_approval"})
        _schedule(state, supervisor, {**record, "status": "awaiting_approval"}, context)


def end_private(origin):
    directory = gideon_home() / "workflow_batches"
    for path in directory.glob("*.json"):
        record = read(path.stem)
        if record is not None and record.get("origin_chat_key") == origin:
            path.unlink(missing_ok=True)
            GrantBook("workflow_batch_waits").revoke(record["name"])
            GrantBook("workflow_batch_starts").revoke(record["name"])


async def _restore_context(record, state, supervisor):
    from gideon.extensions.apps.app_work import from_record
    from gideon.security.approval_answer import principal_from_record
    from gideon.security.durable_work import (
        accepted_origin_values,
        restore_accepted_origin,
    )

    origin = restore_accepted_origin(record["source"])
    values = accepted_origin_values(origin)
    if values is None or values.get("memory_mode") == "temporary":
        return None
    log = getattr(state, "conversation_log", None)
    key = record["origin_chat_key"].removeprefix("dashboard:")
    if log is None:
        return None
    metadata = log.get_metadata(key)
    if (
        not metadata
        or metadata.get("closed")
        or metadata.get("lifecycle", "active") != "active"
        or metadata.get("memory_mode", "persistent") != values["memory_mode"]
        or principal_from_record(metadata.get("initiator"))
        != principal_from_record(values["initiator"])
        or str(metadata.get("created_by_app") or "")
        != str(values.get("created_by_app") or "")
    ):
        return None
    if values.get("memory_mode") == "incognito":
        if not _private_envelope(values):
            return None
        try:
            await _restore_private(supervisor, record, values)
        except Exception:
            return None
    work = from_record(record.get("app_scope"))
    if work is not None and not work.current_tier():
        return None
    return {
        "accepted_origin": origin,
        "session_key": record["session_key"],
        "origin_chat_key": record["origin_chat_key"],
        "work_memory_mode": values["memory_mode"],
        "work_principal": principal_from_record(values["work_actor"]),
        "work_initiator": principal_from_record(values["initiator"]),
        "app_work": work,
    }


def _read_start_grant(state, context):
    from types import SimpleNamespace

    from gideon.security.approval_answer import OWNER, principal_from_record
    from gideon.security.durable_work import accepted_origin_values

    manager = getattr(state, "subagents", None)
    values = accepted_origin_values(context.get("accepted_origin"))
    if manager is None or values is None:
        return None
    app_work = context.get("app_work")
    if app_work is None and principal_from_record(values["initiator"]).kind != OWNER:
        return None
    return manager._spawn_permission(
        SimpleNamespace(
            app_work=app_work,
            parent_session_key=context["session_key"],
            approval_mode="",
        )
    )


def _private_envelope(values):
    model = values.get("execution_model")
    runtime = values.get("execution_runtime")
    return bool(
        isinstance(model, str)
        and model
        and values.get("allowed_models") == [model]
        and isinstance(runtime, str)
        and (runtime == "native" or runtime.startswith("acp:") and len(runtime) > 4)
    )


async def _restore_private(supervisor, record, values):
    from gideon.automation.workflows.private_work import restore_wait_receipt
    from gideon.security.approval_answer import principal_from_record

    return await restore_wait_receipt(
        supervisor,
        record.get("private_receipt"),
        origin=values["origin_session_key"],
        memory_mode=values["memory_mode"],
        original_actor=principal_from_record(values["initiator"]).label,
    )


async def private_receipt_for(supervisor, admission, origin):
    from gideon.security.durable_work import accepted_origin_values

    if not isinstance(admission, BatchAdmission):
        raise RuntimeError("private wait admission unavailable")
    record = read(admission.name)
    values = accepted_origin_values(origin)
    if (
        record is None
        or _content(record) != admission.content
        or values is None
        or not _private_envelope(values)
        or record["source"]
        != {"payload": origin.payload, "signature": origin.signature}
        or not GrantBook("workflow_batch_starts").holds(
            admission.name, _start_content(record)
        )
    ):
        raise RuntimeError("private wait has no current exact batch Allow")
    return await _restore_private(supervisor, record, values)


def relay_event(state, kind, agent, extra):
    from gideon.automation.workflows import ownership, store
    from gideon.security.durable_work import recorded_run_origin

    parsed = ownership.parse_owned(agent.parent_session_key)
    if parsed is None:
        return False
    run = store.get(parsed[0])
    if run is None or RUN_KEY not in run.extra or recorded_run_origin(run) is None:
        return False
    record = read(run.extra[RUN_KEY].get("name", ""))
    if record is None or not GrantBook("workflow_batch_starts").holds(
        record["name"], _start_content(record)
    ):
        return False
    row = next(
        (row for row in _tasks(record["spec"]) if row["node_id"] == parsed[1]), None
    )
    if row is None:
        return False
    payload = {
        **extra,
        "id": agent.id,
        "session": record["origin_chat_key"].removeprefix("dashboard:"),
        "run_id": run.id,
        "node_id": row["node_id"],
        "task": row["label"],
        "agent": agent.agent,
    }
    state.broadcast_ws(kind, payload)
    return True
