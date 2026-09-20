from gideon.assurance.ledger.kinds import STEP_COMPLETED
from gideon.assurance.ledger.reader import run_totals
from gideon.assurance.ledger.writer import EVENTS_FILE
from gideon.automation.workflows import store
from gideon.automation.workflows.controller import RunController
from gideon.automation.workflows.models import WorkflowRun


def test_unrecorded_usage_preserves_observed_totals(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "config_dir", lambda: tmp_path)
    run = store.create(WorkflowRun(id="", workflow_name="usage", total_tokens=37))
    store.append_jsonl(
        run.id,
        EVENTS_FILE,
        {"kind": STEP_COMPLETED, "tokens": 11, "cost_usd": 0.25},
    )
    store.append_jsonl(run.id, EVENTS_FILE, {"kind": STEP_COMPLETED})

    totals = run_totals(store, run.id)

    assert totals["tokens"] is None
    assert totals["cost_usd"] == 0.25
    assert totals["priced"] is False
    controller = RunController(run, {"root": {"kind": "sequence"}})
    controller._restore_recorded_tokens(totals)
    assert run.total_tokens == 37
