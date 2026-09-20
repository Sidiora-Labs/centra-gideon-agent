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
