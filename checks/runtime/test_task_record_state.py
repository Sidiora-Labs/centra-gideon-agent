import json
from dataclasses import fields

import pytest

from gideon.engine.tasks.models import (
    Project,
    Task,
    TaskDependency,
    TaskList,
    TaskStatus,
    WorkflowTaskBinding,
    coerce_task_field,
)


def test_actual_json_roundtrip_retains_workflow_proof_and_detaches_nested_values():
    binding = WorkflowTaskBinding("run", "node", managed=False, fingerprint="stable")
    task = Task(
        id="record",
        title="Original",
        workflow_binding=binding,
        evidence=[{"kind": "gate", "details": {"measurements": [1, 2]}}],
        dependencies=[TaskDependency("prerequisite")],
        exit_criteria=[{"criteria": "Checked", "status": "DONE", "comment": "proof"}],
        action_plan=[{"description": "Execute", "sequence": "3", "completed": True}],
        notes=[
            {"content": "Observation", "created_at": "2026-09-16", "phase": "research"}
        ],
    )
    wire = task.to_dict()
    assert set(wire) == {field.name for field in fields(Task)}
    wire["evidence"][0]["details"]["measurements"].append(3)
    assert task.evidence[0]["details"]["measurements"] == [1, 2]
    loaded = Task.from_dict(json.loads(json.dumps(wire)))
    assert loaded.workflow_binding.managed is False
    assert loaded.workflow_binding.fingerprint == "stable"
    assert loaded.prerequisite_ids() == ["prerequisite"]
    assert loaded.can_mark_complete() and loaded.incomplete_exit_criteria() == []
    assert loaded.to_dict()["notes"] == [
        {"content": "Observation", "timestamp": "2026-09-16", "phase": "research"}
    ]
    assert loaded.to_dict()["evidence"][0]["details"]["measurements"] == [1, 2, 3]


def test_salvage_defaults_do_not_change_strict_write_admission():
    record = Task.from_dict(
        {
            "id": "damaged",
            "title": {"bad": True},
            "status": "unknown",
            "order": True,
            "labels": [{"bad": True}, 42],
            "dependencies": [],
            "depends_on": ["legacy"],
            "evidence": [17, {"kind": "proof"}],
        }
    )
    assert (
        record.id == "damaged"
        and record.title == ""
        and record.status is TaskStatus.OPEN
    )
    assert record.order == 0 and record.labels == ["", "42"]
    assert record.prerequisite_ids() == ["legacy"]
    assert record.evidence == [{"kind": "proof"}]
    for name, value in [
        ("title", {}),
        ("order", True),
        ("evidence", [17]),
        ("workflow_binding", "run"),
    ]:
        with pytest.raises(ValueError):
            coerce_task_field(name, value)


def test_hierarchy_legacy_defaults_and_passthrough_fields_keep_wire_shape():
    project = Project.from_dict(
        {
            "id": "legacy",
            "name": "Personal",
            "is_builtin": False,
            "agent_instructions_template": None,
            "status": None,
        }
    )
    assert project.to_dict()["is_builtin"] is True
    assert project.agent_instructions_template is None and project.status == "active"
    task_list = TaskList.from_dict(
        {"id": "list", "name": None, "project_id": "legacy", "ignored": True}
    )
    assert task_list.name is None
    assert set(task_list.to_dict()) == {field.name for field in fields(TaskList)}
