"""A rewind closes the old ask and creates a distinct ask for the new epoch."""

import pytest

from gideon.automation.workflows import human_input, store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))


def _spec() -> dict:
    return {
        "name": "rewind-at-gate",
        "root": {
            "kind": "sequence",
            "id": "sequence",
            "children": [
                {
                    "kind": "gate",
                    "id": "approval",
                    "config": {"kind": "approval", "prompt": "Ship?", "timeout_secs": 0},
                }
            ],
        },
    }


async def test_rewind_replaces_the_ask_and_rejects_its_old_token():
    spec = _spec()
    run = store.create(WorkflowRun(id="", workflow_name="rewind-at-gate", mode="background"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    assert await controller.run_to_completion(timeout=10) == RunStatus.NEEDS_INPUT
    old = human_input.list_continuations(run.id)[0]

    queued = controller.submit_mutation([{"op": "rewind", "node_id": "approval"}], confirm=True)
    assert queued["ok"] and queued["queued"]
    assert await controller.run_to_completion(timeout=10) == RunStatus.NEEDS_INPUT
    assert human_input.load_continuation(run.id, old.token) is None

    new = human_input.list_continuations(run.id)[0]
    assert new.token != old.token
    assert new.confirmation_id != old.confirmation_id
    assert not controller.resume(old.token, True, responder="owner:qualifier")["ok"]
