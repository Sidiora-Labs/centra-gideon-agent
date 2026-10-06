from __future__ import annotations

import pytest

from gideon.automation.workflows import journal, store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import Node, RunStatus, WorkflowRun, walk


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))


def transform(node_id):
    return {
        "kind": "transform",
        "id": node_id,
        "config": {"expr": {"said": "as written"}},
    }


def paused_controller():
    spec = {
        "name": "edited",
        "root": {
            "kind": "sequence",
            "id": "s",
            "children": [transform("draft"), transform("publish")],
        },
    }
    run = store.create(
        WorkflowRun(id="", workflow_name="edited", status=RunStatus.PAUSED)
    )
    store.write_spec(run.id, spec)
    return RunController(run, spec, services=EngineServices())


def say(node_id, words):
    return {
        "op": "update_node",
        "node_id": node_id,
        "fields": {"expr": {"said": words}},
    }


def restart_and_drain(controller):
    persisted = store.get(controller.run.id)
    assert persisted.status == RunStatus.PAUSED
    assert len(persisted.extra["workflow_queued_mutations"]) == 2
    fresh = RunController(
        persisted, store.read_spec(persisted.id), services=EngineServices()
    )
    fresh._drain_mutations()
    assert fresh._pending_mutations == []
    assert store.get(persisted.id).extra["workflow_queued_mutations"] == []
    return fresh


def said(run_id):
    root = Node.from_dict(store.read_spec(run_id)["root"])
    return {
        node.id: node.config["expr"]["said"]
        for _, node in walk(root)
        if node.kind.value == "transform"
    }


def test_two_distinct_edits_survive_restart_and_apply_in_order():
    controller = paused_controller()
    version = controller.run.spec_version
    assert (
        controller.submit_mutation([say("draft", "first edit")], confirm=True)["queued"]
        is True
    )
    assert (
        controller.submit_mutation([say("publish", "second edit")], confirm=True)[
            "queued"
        ]
        is True
    )
    fresh = restart_and_drain(controller)
    assert said(fresh.run.id) == {"draft": "first edit", "publish": "second edit"}
    assert fresh.run.spec_version == version + 2
    edits = journal.journal_records(
        fresh.run.id, kinds={journal.USER_EDITED_MID_FLIGHT}
    )
    assert [record["ops"][0]["fields"]["expr"]["said"] for record in edits] == [
        "first edit",
        "second edit",
    ]


def test_later_input_edit_wins_after_restart_and_queue_stays_empty():
    controller = paused_controller()
    assert (
        controller.submit_mutation(
            [{"op": "set_input", "overrides": {"since": "6h"}}], confirm=True
        )["queued"]
        is True
    )
    assert (
        controller.submit_mutation(
            [{"op": "set_input", "overrides": {"since": "24h"}}], confirm=True
        )["queued"]
        is True
    )
    fresh = restart_and_drain(controller)
    assert fresh.run.inputs["since"] == "24h"
    restarted = RunController(
        store.get(fresh.run.id),
        store.read_spec(fresh.run.id),
        services=EngineServices(),
    )
    assert restarted._pending_mutations == []
    assert restarted.run.inputs["since"] == "24h"
    edits = journal.journal_records(
        fresh.run.id, kinds={journal.USER_EDITED_MID_FLIGHT}
    )
    assert [record["ops"][0]["overrides"]["since"] for record in edits] == ["6h", "24h"]


def test_edit_can_build_on_prior_queued_insert():
    controller = paused_controller()
    insert = {"op": "insert", "parent_id": "s", "index": 2, "node": transform("notify")}
    assert controller.submit_mutation([insert], confirm=True)["queued"] is True
    assert (
        controller.submit_mutation([say("notify", "told them")], confirm=True)["queued"]
        is True
    )
    fresh = restart_and_drain(controller)
    assert said(fresh.run.id) == {
        "draft": "as written",
        "publish": "as written",
        "notify": "told them",
    }
