from __future__ import annotations

import pytest

from gideon.automation.workflows import store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import FailureClass, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setattr("gideon.automation.workflows.store.config_dir", lambda: home)


@pytest.mark.parametrize(
    ("declared_type", "value", "actual_type"),
    [
        ("string", {"wrong": "shape"}, "object"),
        ("object", "wrong shape", "string"),
        ("number", True, "boolean"),
        ("integer", 1.5, "number"),
        ("boolean", 1, "number"),
        ("array", "not an array", "string"),
    ],
)
async def test_declared_input_type_fails_before_stage_invocation(
    declared_type, value, actual_type
) -> None:
    spec = {
        "name": "typed-stage",
        "inputs": {"subject": {"type": declared_type, "required": True}},
        "root": {"kind": "stage", "id": "work", "config": {"prompt": "do work"}},
    }
    run = store.create(
        WorkflowRun(id="", workflow_name="typed-stage", inputs={"subject": value})
    )
    store.write_spec(run.id, spec)

    controller = RunController(run, spec, services=EngineServices(subagents=None))

    assert await controller.run_to_completion(timeout=20) == RunStatus.FAILED
    failure = controller.instances["root"].failure
    assert failure is not None
    assert failure.failure_class == FailureClass.USER
    assert failure.cause_plain == (
        f"workflow input 'subject' declares type '{declared_type}' "
        f"but received '{actual_type}'"
    )
    assert failure.remediation == f"provide 'subject' as a {declared_type}"


async def test_declared_values_are_typed_before_a_run_is_persisted() -> None:
    from gideon.automation.workflows.service import _coerce_declared_inputs

    spec = {"inputs": {
        "count": {"type": "integer"},
        "ratio": {"type": "number"},
        "enabled": {"type": "boolean"},
        "items": {"type": "array"},
        "options": {"type": "object"},
    }}
    values, invalid = _coerce_declared_inputs(spec, {
        "count": "3", "ratio": "1.5", "enabled": "false",
        "items": '["one"]', "options": '{"mode":"safe"}',
    })
    assert invalid == []
    assert values == {
        "count": 3, "ratio": 1.5, "enabled": False,
        "items": ["one"], "options": {"mode": "safe"},
    }

    _, invalid = _coerce_declared_inputs(spec, {"count": "3.5", "items": "{}"})
    assert len(invalid) == 2
    assert "count" in invalid[0]
    assert "items" in invalid[1]

    exact, invalid = _coerce_declared_inputs(spec, {"count": "9007199254740993"})
    assert invalid == []
    assert exact["count"] == 9007199254740993
