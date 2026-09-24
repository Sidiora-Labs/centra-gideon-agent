from __future__ import annotations

from dataclasses import asdict

import pytest

from gideon.automation.workflows import defs, service, store
from gideon.automation.workflows.bundled_defs import read_template
from gideon.automation.workflows.models import Node
from gideon.automation.workflows.supervisor_policy import (
    KIND_CONVERGENCE,
    ConvergenceSpec,
    parse_supervisor_policy,
)
from gideon.automation.workflows.validator import validate_spec
from gideon.automation.workflows.watchdog import WorkflowWatchdog


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _spec(convergence):
    return {
        "name": "convergence",
        "root": {
            "kind": "loop",
            "id": "loop",
            "config": {
                "mode": "counted",
                "n": 1,
                "supervisor": {"convergence": convergence},
            },
            "body": {"kind": "transform", "id": "step", "config": {"expr": 1}},
        },
    }


@pytest.mark.parametrize("declared", list(KIND_CONVERGENCE.values()))
def test_every_declared_convergence_round_trips_and_validates(declared):
    raw = asdict(declared)
    assert parse_supervisor_policy({"convergence": raw}).convergence == declared
    assert validate_spec(_spec(raw)).ok


@pytest.mark.parametrize(
    "raw,code",
    [
        (None, "WF_CONVERGENCE_NOT_OBJECT"),
        ([], "WF_CONVERGENCE_NOT_OBJECT"),
        ({"signal": "unknown"}, "WF_CONVERGENCE_BAD_SIGNAL"),
        ({"signal": []}, "WF_CONVERGENCE_BAD_SIGNAL"),
        ({"unexpected": True}, "WF_CONVERGENCE_UNKNOWN_FIELD"),
        ({"stagnation_enabled": "false"}, "WF_CONVERGENCE_BAD_TYPE"),
        ({"done_check_optional": 1}, "WF_CONVERGENCE_BAD_TYPE"),
        ({"criteria_key": []}, "WF_CONVERGENCE_BAD_TYPE"),
        ({"signal": "verify_command"}, "WF_CONVERGENCE_MISSING_COMMAND_KEY"),
    ],
)
def test_invalid_convergence_has_typed_authoring_errors(raw, code):
    assert code in {issue.code for issue in validate_spec(_spec(raw)).errors}


def test_malformed_persisted_convergence_keeps_typed_defaults():
    assert (
        parse_supervisor_policy(
            {
                "convergence": {
                    "signal": [],
                    "command_key": [],
                    "done_check_optional": "false",
                    "stagnation_enabled": 0,
                    "unknown": True,
                }
            }
        ).convergence
        == ConvergenceSpec()
    )


def test_general_bundled_declaration_equals_kind_policy():
    definition = read_template("general-project")
    assert definition is not None
    spec = definition.to_dict()
    assert validate_spec(spec).ok
    declaration = spec["root"]["config"]["supervisor"]
    assert (
        parse_supervisor_policy(declaration).convergence == KIND_CONVERGENCE["general"]
    )


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(defs, "_providers", {})


@pytest.mark.anyio
async def test_general_launch_uses_bundled_definition_and_real_controller(
    isolated_store,
):
    supervisor = WorkflowWatchdog()
    try:
        preflight = await service.start_kind_run(
            kind="general",
            inputs={"task": "inspect the task"},
            supervisor=supervisor,
        )
        assert preflight["code"] == "WF_RUN_PREFLIGHT_FAILED"
        result = await service.start_kind_run(
            kind=" General ",
            inputs={"task": "inspect the task"},
            supervisor=supervisor,
            skip_preflight=True,
        )
        assert result["ok"], result
        run = store.get(result["run_id"])
        assert run.workflow_name == "general-project"
        assert run.inputs["task"] == "inspect the task"
        assert run.inputs["exit_condition"]
        spec = store.read_spec(run.id)
        assert spec == read_template("general-project").to_dict()
        controller = supervisor.controller(run.id)
        assert controller is not None
        assert (
            controller._supervisor_policy(Node.from_dict(spec["root"])).convergence
            == KIND_CONVERGENCE["general"]
        )
    finally:
        await supervisor.stop()


@pytest.mark.anyio
async def test_kind_launch_retains_required_input_and_unknown_kind_errors(
    isolated_store,
):
    missing = await service.start_kind_run(kind="general")
    assert missing["code"] == "WF_RUN_MISSING_INPUTS"
    assert missing["missing"] == ["task"]
    unsupported = await service.start_kind_run(kind="not-a-kind")
    assert unsupported["code"] == "WF_KIND_NOT_SUPPORTED"
    assert store.list_runs() == ([], 0)
