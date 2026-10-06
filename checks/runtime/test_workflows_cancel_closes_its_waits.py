"""Cancellation after a persisted gate wait closes that exact ask."""

import pytest

from gideon.automation.workflows import human_input, journal, store
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


async def test_restart_cancel_closes_continuation_and_records_withdrawal():
    spec = {
        "name": "cancel-wait",
        "root": {
            "kind": "gate",
            "id": "approval",
            "config": {"kind": "approval", "prompt": "Continue?", "timeout_secs": 0},
        },
    }
    run = store.create(
        WorkflowRun(id="", workflow_name="cancel-wait", mode="background")
    )
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    assert await controller.run_to_completion(timeout=10) == RunStatus.NEEDS_INPUT
    continuation = human_input.list_continuations(run.id)[0]

    controller.request_cancel()
    assert await controller.run_to_completion(timeout=10) == RunStatus.CANCELLED
    assert human_input.load_continuation(run.id, continuation.token) is None
    resolved = [
        row
        for row in journal.ledger(run.id)
        if row.get("kind") == journal.GATE_RESOLVED
        and row.get("node_id") == continuation.node_id
    ]
    assert len(resolved) == 1 and resolved[0].get("outcome") == "withdrawn"
