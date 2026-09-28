from __future__ import annotations

import asyncio

from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.review import TriggerReviewStore
from gideon.automation.triggers.store import TriggerStore
from gideon.automation.workflows import defs as defs_mod
from gideon.automation.workflows.bundled_defs import register_bundled_provider
from gideon.integrations.action_providers.run_workflow_provider import (
    RunWorkflowActionProvider,
    _validated_inputs,
)


def test_run_workflow_uses_current_definition_required_and_typed_inputs(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    previous = dict(defs_mod._providers)
    try:
        register_bundled_provider()
        provider = RunWorkflowActionProvider()

        assert "missing required input(s): thesis" in asyncio.run(
            provider.config_problem(
                {"workflow": "thesis-tracker", "inputs": {"evidence": "sample"}}
            )
        )
        assert "input 'thesis' declares string" in asyncio.run(
            provider.config_problem(
                {"workflow": "thesis-tracker", "inputs": {"thesis": 7}}
            )
        )
        assert asyncio.run(
            provider.config_problem(
                {"workflow": "thesis-tracker", "inputs": {"thesis": "A testable claim"}}
            )
        ) == ""

        loaded = asyncio.run(
            defs_mod.get_provider("bundled").get_def("thesis-tracker")
        )
        normalized, problem = _validated_inputs(
            loaded.to_dict(), {"thesis": "A testable claim"}
        )
        assert problem == ""
        assert normalized == {"thesis": "A testable claim", "evidence": ""}
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(previous)


def test_run_workflow_refuses_revised_definition_missing_old_trigger_inputs(
    tmp_path, monkeypatch
):
    from gideon.automation.workflows.bundled_defs import read_template
    from gideon.automation.workflows.native_defs import register_native_provider
    from gideon.integrations.action_providers.base import ActionContext

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    previous = dict(defs_mod._providers)
    try:
        register_native_provider()
        native = defs_mod.get_provider("native")
        template = read_template("thesis-tracker")
        assert native is not None and template is not None
        name = "trigger-input-change"
        root = template.to_dict()["root"]
        initial_inputs = {"thesis": {"type": "string", "required": True}}
        asyncio.run(
            native.save_def(name=name, root=root, inputs=initial_inputs, create_only=True)
        )
        provider = RunWorkflowActionProvider()
        action = {"workflow": name, "inputs": {"thesis": "A saved trigger value"}}
        assert asyncio.run(provider.config_problem(action)) == ""

        changed_inputs = {
            **initial_inputs,
            "evidence": {"type": "string", "required": True},
        }
        asyncio.run(
            native.save_def(
                name=name,
                root=root,
                inputs=changed_inputs,
                expected_revision=1,
            )
        )
        result = asyncio.run(
            provider.execute(action, ActionContext(event="clock", context="trigger:test"))
        )
        assert result.success is False
        assert result.error == f"workflow '{name}': missing required input(s): evidence"
        assert "edit the trigger" in result.stderr
    finally:
        defs_mod._providers.clear()
        defs_mod._providers.update(previous)


def test_missed_review_is_durable_single_decision_with_canonical_trigger(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    trigger_store = TriggerStore(base_dir=tmp_path)
    trigger_store.upsert(
        Trigger(
            id="clock:review-vector",
            name="Review vector",
            kind="clock",
            spec={"kind": "interval", "interval_secs": 900},
            workflow={
                "inline": {
                    "provider": "run-workflow",
                    "config": {"workflow": "thesis-tracker", "inputs": {"thesis": "T"}},
                }
            },
        )
    )
    review_store = TriggerReviewStore(tmp_path)
    report = {
        "review": {
            "rows": [
                {"trigger_id": "clock:review-vector", "scheduled_for": 1200.0}
            ],
            "summaries": [],
        }
    }

    pending = review_store.add_boot_observations(
        trigger_store, report, [], now=1500.0
    )
    assert len(pending) == 1
    card = pending[0]
    assert card["id"] == "missed:clock:review-vector"
    assert card["trigger_id"] == "store:clock:review-vector"
    assert card["missed_count"] == 1
    assert card["latest_missed_at"] == 1200.0
    assert card["action_revision"]
    assert card["frozen_action_fingerprint"] == card["action_revision"]
    assert review_store.resolve(
        card["id"], decision="dismiss", outcome="dismissed"
    )

    assert review_store.add_boot_observations(
        trigger_store, report, [], now=1800.0
    ) == []
    reloaded = TriggerReviewStore(tmp_path).get(card["id"])
    assert reloaded is not None
    assert reloaded["status"] == "resolved"
    assert reloaded["decision"] == "dismiss"
