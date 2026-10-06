"""Real controller outcomes name causative failures and truthful continuations."""

from __future__ import annotations

import json

import pytest

from gideon.automation.workflows import journal as J
from gideon.automation.workflows import store
from gideon.automation.workflows.controller import EngineServices, RunController
from gideon.automation.workflows.models import InstanceState, RunStatus, WorkflowRun

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    return home


def _gate(*, allow_failure: bool = False) -> dict:
    config = {"kind": "expression", "expr": "1 == 2", "on_error": "null_continue"}
    if allow_failure:
        config["allow_failure"] = True
    return {"kind": "gate", "id": "check", "label": "source check", "config": config}


def _transform(node_id: str) -> dict:
    return {"kind": "transform", "id": node_id, "config": {"expr": {"ran": node_id}}}


async def _run(children: list[dict]) -> tuple[RunController, list[dict]]:
    spec = {
        "name": "terminal-sentence",
        "root": {"kind": "sequence", "id": "steps", "children": children},
    }
    run = store.create(
        WorkflowRun(id="", workflow_name=spec["name"], mode="background")
    )
    store.write_spec(run.id, spec)
    published: list[dict] = []

    def publish(event: str, payload: dict) -> None:
        if event == "workflow_run_update":
            published.append(dict(payload))

    controller = RunController(run, spec, services=EngineServices(publish=publish))
    await controller.run_to_completion(timeout=10)
    return controller, published


def _finished(run_id: str) -> dict:
    records = J.journal_records(run_id, kinds={J.RUN_FINISHED})
    assert len(records) == 1
    return records[0]


async def test_failed_gate_that_runs_its_sequential_follower_has_one_truthful_sentence():
    controller, published = await _run([_gate(), _transform("publish")])

    sentence = "The run continued past “source check”, which failed: gate condition is false: 1 == 2."
    assert controller.run.status == RunStatus.FAILED
    assert controller.instances["root.children[1]"].state == InstanceState.DONE
    assert controller.run.error_message == sentence
    assert store.get(controller.run.id).error_message == sentence
    assert _finished(controller.run.id)["error"] == sentence
    assert {key: published[-1].get(key) for key in ("status", "error")} == {
        "status": "failed",
        "error": sentence,
    }


async def test_failed_last_step_does_not_claim_a_continuation():
    controller, _ = await _run([_gate()])

    assert controller.run.status == RunStatus.FAILED
    assert (
        controller.run.error_message
        == "“source check” failed: gate condition is false: 1 == 2."
    )


async def test_parallel_successful_sibling_is_not_reported_as_continued_work():
    parallel = {
        "kind": "parallel",
        "id": "parallel",
        "children": [_gate(), _transform("beside")],
    }
    controller, _ = await _run([parallel])

    assert controller.run.status == RunStatus.FAILED
    assert (
        controller.instances["root.children[0].children[1]"].state == InstanceState.DONE
    )
    assert (
        controller.run.error_message
        == "“source check” failed: gate condition is false: 1 == 2."
    )


async def test_tolerated_failure_is_omitted_and_clean_completion_has_no_sentence():
    tolerated, _ = await _run([_gate(allow_failure=True), _transform("publish")])
    clean, _ = await _run([_transform("draft"), _transform("publish")])

    assert tolerated.run.status == RunStatus.COMPLETE
    assert tolerated.run.error_message == ""
    assert clean.run.status == RunStatus.COMPLETE
    assert clean.run.error_message == ""


async def test_restart_review_history_keeps_the_actual_action_outcome_and_trace(
    tmp_path,
):
    from gideon.automation.schedule_history import ExecutionJournal, ExecutionRecord
    from gideon.automation.triggers.review import record_review_outcome

    journal = ExecutionJournal(tmp_path)
    action = ExecutionRecord(
        run_id="manual-run-1",
        job_id="trigger-1",
        trigger="manual",
        status="queued",
        summary="Workflow 'daily-brief' queued behind a run already in flight.",
        trace=json.dumps({"run_id": "workflow-run-7", "queued": True}),
    )
    await journal.append(action)
    persisted_action = await journal.get_run("trigger-1", action.run_id)

    review_id = await record_review_outcome(
        {"trigger_id": "store:trigger-1", "reason": "interrupted"},
        "interrupted_retried",
        action_record=persisted_action,
        base_dir=tmp_path,
    )
    latest, _ = await journal.list_for_job("trigger-1", 0, 1)
    persisted_review = await journal.get_run("trigger-1", review_id)

    assert latest[0]["run_id"] == review_id
    assert latest[0]["status"] == "queued"
    assert latest[0]["summary"] == action.summary
    assert persisted_review["trigger"] == "review"
    assert persisted_review["trace"] == action.trace


def test_action_summaries_keep_structured_provider_output_in_trace():
    from gideon.automation.schedule_history import ExecutionRecord, action_summary
    from gideon.integrations.action_providers.base import ActionResult

    raw = json.dumps({"workflow": "daily-brief", "run_id": "r1", "started": True})
    result = ActionResult(True, stdout=raw, outcome="launched")
    summary = action_summary("launched", result)
    record = ExecutionRecord(status="launched", summary=summary, trace=result.stdout)

    assert summary == "Workflow 'daily-brief' launched."
    assert record.summary != record.trace
    assert json.loads(record.trace)["run_id"] == "r1"
