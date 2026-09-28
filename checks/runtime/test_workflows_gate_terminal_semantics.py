"""A denied gate terminates the run before any following stage can execute."""

import pytest

from gideon.automation.workflows import human_input, store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))


async def test_declined_gate_stops_following_stage():
    spec = {
        "name": "terminal-gate",
        "root": {
            "kind": "sequence",
            "id": "sequence",
            "children": [
                {
                    "kind": "gate",
                    "id": "approval",
                    "config": {"kind": "approval", "prompt": "Ship?", "timeout_secs": 0},
                },
                {"kind": "transform", "id": "after", "config": {"expr": "must not run"}},
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="terminal-gate", mode="background"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    assert await controller.run_to_completion(timeout=10) == RunStatus.NEEDS_INPUT
    token = human_input.list_continuations(run.id)[0].token

    answer = controller.resume(token, False, responder="owner:qualifier")
    assert answer["ok"]
    assert await controller.run_to_completion(timeout=10) == RunStatus.DECLINED
    assert all(
        instance.state != InstanceState.DONE
        for path, instance in controller.instances.items()
        if path.endswith("children[1]")
    )
