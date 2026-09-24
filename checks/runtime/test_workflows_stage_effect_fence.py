from __future__ import annotations

import asyncio
import time

import pytest

from gideon.automation.workflows import store
from gideon.automation.workflows.controller import (
    EngineServices,
    RunController,
    _InFlight,
)
from gideon.automation.workflows.effects import EffectStatus, effect_history
from gideon.automation.workflows.engine import dispatcher_commits_effects
from gideon.automation.workflows.engine_support import NodeResult
from gideon.automation.workflows.journal import CacheKey
from gideon.automation.workflows.models import (
    InstanceState,
    Node,
    NodeInstance,
    WorkflowRun,
)
from gideon.automation.workflows.tick import ReadyNode


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("stage", True),
        ("action", True),
        ("infer", False),
        ("transform", False),
        ("visualize", False),
        ("gate", False),
        ("sequence", False),
    ],
)
def test_selected_dispatcher_effect_membership(kind, expected):
    assert (
        dispatcher_commits_effects(Node.from_dict({"kind": kind, "id": "work"}))
        is expected
    )


@pytest.mark.anyio
@pytest.mark.parametrize("verb", ["rewind", "run_from"])
@pytest.mark.parametrize("restored_state", [InstanceState.DONE, InstanceState.PENDING])
async def test_stage_commit_survives_restart_and_requires_cascade_consent(
    tmp_path, monkeypatch, verb, restored_state
):
    monkeypatch.setattr(
        "gideon.automation.workflows.store.config_dir", lambda: tmp_path
    )
    spec = {
        "name": "stage-fence",
        "root": {
            "kind": "sequence",
            "id": "flow",
            "children": [
                {"kind": "transform", "id": "seed", "config": {"expr": "input"}},
                {
                    "kind": "stage",
                    "id": "work",
                    "config": {
                        "prompt": "Process {{nodes.seed.output}}",
                        "capability": "mutating",
                    },
                },
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name=spec["name"]))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices())
    path = "root.children[1]"
    node = Node.from_dict(spec["root"]["children"][1])
    ready = ReadyNode(path=path, node=node, lane="agent")
    result = NodeResult(state=InstanceState.DONE, output={"id": "stage-resource"})
    controller.instances[path] = NodeInstance(path=path, state=InstanceState.RUNNING)
    controller._record_effect(ready, controller.instances[path], EffectStatus.ATTEMPTED)
    entry = _InFlight(
        task=asyncio.current_task(),
        ready=ready,
        started=time.time(),
        last_progress=time.time(),
        cache_key=CacheKey(path, 0, "inputs", "spec"),
    )
    controller._apply(entry, result)
    records = effect_history(run.id)[path]
    assert [record.effect_status for record in records] == [
        EffectStatus.ATTEMPTED,
        EffectStatus.COMMITTED,
    ]
    assert records[-1].output_id == "stage-resource"
    controller.instances[path].state = restored_state
    store.write_state(run.id, controller.instances)

    restarted = RunController(run, spec, services=EngineServices())
    before = store.read_state(run.id)[path].to_dict()
    refused = restarted.submit_mutation([{"op": verb, "node_id": "seed"}])
    assert refused["code"] == "WF_MUT_CONFIRM_REQUIRED"
    assert refused["preview"]["committed_effects"] == ["work"]
    assert refused["preview"]["needs_confirmation"] is True
    assert restarted._pending_mutations == []
    assert store.read_state(run.id)[path].to_dict() == before
    accepted = restarted.submit_mutation(
        [{"op": verb, "node_id": "seed"}],
        confirm=True,
    )
    assert accepted["queued"] is True

    restarted.instances[path].epoch = 1
    assert not await restarted._effect_preflight(ready, restarted.instances[path])
    assert restarted.instances[path].failure.terminal_reason == "committed_effect"
