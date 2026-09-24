from __future__ import annotations

import pytest

from gideon.automation.workflows import service, store
from gideon.automation.workflows.models import (
    InstanceState,
    NodeInstance,
    WorkflowRun,
    spec_path,
)


@pytest.mark.parametrize(
    ("instance", "expected"),
    [
        ("root", "root"),
        (
            "root.body@12.children[1].body#3.children[0]",
            "root.body.children[1].body.children[0]",
        ),
        ("root.body#1.body@2.body#10", "root.body.body.body"),
        ("root.children[10].body@1", "root.children[10].body"),
    ],
)
def test_spec_path_preserves_all_segments(instance, expected):
    assert spec_path(instance) == expected
    assert spec_path(expected) == expected


@pytest.fixture
def nested_run(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    run = store.create(WorkflowRun(id="", workflow_name="nested-paths"))
    store.write_spec(
        run.id,
        {
            "name": "nested-paths",
            "root": {
                "kind": "loop",
                "id": "loop",
                "body": {
                    "kind": "sequence",
                    "id": "cycle",
                    "children": [
                        {
                            "kind": "foreach",
                            "id": "source",
                            "body": {
                                "kind": "transform",
                                "id": "record",
                                "config": {"expr": 1},
                            },
                        },
                        {
                            "kind": "foreach",
                            "id": "other",
                            "body": {
                                "kind": "transform",
                                "id": "consumer",
                                "config": {"expr": "{{nodes.source.output}}"},
                            },
                        },
                    ],
                },
            },
        },
    )
    outputs = {
        "root.body@0": "cycle output",
        "root.body@0.children[0]": "source output",
        "root.body@0.children[0].body#0": "first record",
        "root.body@0.children[0].body#1": "second record",
        "root.body@0.children[1].body#0": "consumer output",
        "root.body@1.children[1].body#0": "later consumer",
        "root.body@1.children[1].body#1": "last consumer",
    }
    store.write_state(
        run.id,
        {path: NodeInstance(path=path, state=InstanceState.DONE) for path in outputs},
    )
    for path, output in outputs.items():
        store.write_output(run.id, path, output)
    return run


@pytest.mark.parametrize(
    ("node_id", "expected"),
    [
        ("cycle", "cycle output"),
        ("source", "source output"),
        ("record", "second record"),
        ("consumer", "last consumer"),
    ],
)
def test_output_and_inspection_match_exact_spec_node(nested_run, node_id, expected):
    output = service.output(nested_run.id, node_id)
    inspection = service.inspect_node(nested_run.id, node_id)
    assert output["ok"] is True
    assert inspection["ok"] is True
    assert output["output"] == inspection["output"] == expected
    if node_id == "consumer":
        assert inspection["resolved_inputs"] == {"source": "source output"}


def test_fanout_totals_are_isolated_by_complete_spec_path(nested_run):
    rows = {row["instance_path"]: row for row in service._nodes_of(nested_run.id)}
    records = [row for row in rows.values() if row["node_id"] == "record"]
    consumers = [row for row in rows.values() if row["node_id"] == "consumer"]
    assert len(records) == 2
    assert len(consumers) == 3
    assert {row["item_total"] for row in records} == {2}
    assert {row.get("item_total", 1) for row in consumers} == {1, 2}
    assert "item_total" not in rows["root.body@0"]
