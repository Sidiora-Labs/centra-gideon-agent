from __future__ import annotations

import pytest

from gideon.automation.workflows import service, store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import (
    Node,
    NodeInstance,
    RunStatus,
    WorkflowRun,
    sibling_group,
)
from gideon.automation.workflows.tick import Limits, frontier


def fanout(count=5):
    return {
        "name": "total",
        "root": {
            "kind": "foreach",
            "id": "fan",
            "config": {"items": list(range(count)), "max_concurrency": 1},
            "body": {
                "kind": "sequence",
                "id": "steps",
                "children": [
                    {"kind": "transform", "id": "value", "config": {"expr": "{{item}}"}}
                ],
            },
        },
    }


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))


def test_total_precedes_admission_and_survives_deferral():
    node = Node.from_dict(fanout()["root"])
    ready = frontier(node, {}).ready
    assert len(ready) == 1
    assert ready[0].item_total == 5
    assert ready[0].path == "root.body#0.children[0]"
    held = frontier(
        node, {}, limits=Limits(lanes={"compute": 1}), running_lanes={"compute": 1}
    )
    assert held.ready == []
    assert held.deferred[0].item_total == 5


@pytest.mark.anyio
@pytest.mark.parametrize("count", [1, 5])
async def test_total_survives_start_events_persistence_and_introspection(count):
    spec = fanout(count)
    run = store.create(WorkflowRun(id="", workflow_name="total"))
    store.write_spec(run.id, spec)
    events = []
    controller = RunController(
        run,
        spec,
        services=EngineServices(
            publish=lambda name, payload: events.append((name, payload))
        ),
    )
    assert await controller.run_to_completion(timeout=20) == RunStatus.COMPLETE
    started = [
        payload
        for name, payload in events
        if name == "workflow_node_started" and payload.get("node_id") == "value"
    ]
    assert len(started) == count
    assert {payload["item_total"] for payload in started} == {count}
    instances = store.read_state(run.id)
    leaves = [inst for path, inst in instances.items() if path.endswith(".children[0]")]
    assert len(leaves) == count
    assert {inst.item_total for inst in leaves} == {count}
    rows = [row for row in service._nodes_of(run.id) if row["node_id"] == "value"]
    assert {row["item_total"] for row in rows} == {count}
    assert {row["item_index"] for row in rows} == set(range(count))


def test_instance_codec_retains_total_and_reads_old_records():
    instance = NodeInstance(path="root.body#0", item_total=7)
    assert NodeInstance.from_dict(instance.to_dict()).item_total == 7
    assert NodeInstance.from_dict({"path": "root"}).item_total == 0


def test_sibling_group_preserves_enclosing_iterations():
    assert (
        sibling_group("root.body#0.body@12.children[1]")
        == "root.body#0.body.children[1]"
    )
    assert (
        sibling_group("root.body#1.body@2.children[1]")
        == "root.body#1.body.children[1]"
    )
    assert sibling_group("root") == "root"


def test_loop_counts_do_not_mix_outer_iterations():
    run = store.create(WorkflowRun(id="", workflow_name="counts"))
    paths = [
        "root.body#0.body@0",
        "root.body#0.body@1",
        "root.body#1.body@0",
        "root.body#1.body@1",
        "root.body#1.body@2",
    ]
    store.write_state(run.id, {path: NodeInstance(path=path) for path in paths})
    rows = service._nodes_of(run.id)
    assert {row["item_total"] for row in rows if "#0." in row["instance_path"]} == {2}
    assert {row["item_total"] for row in rows if "#1." in row["instance_path"]} == {3}
