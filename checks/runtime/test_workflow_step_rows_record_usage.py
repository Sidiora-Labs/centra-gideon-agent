from __future__ import annotations

import pytest

from gideon.automation.workflows import journal as journal_mod
from gideon.automation.workflows import store
from gideon.automation.workflows.controller import RunController
from gideon.automation.workflows.journal import Journal
from gideon.automation.workflows.models import (
    Failure,
    FailureClass,
    InstanceState,
    NodeInstance,
    RunStatus,
    WorkflowRun,
)
from gideon.automation.workflows.service import inspect_node, status
from gideon.automation.workflows.step_usage import (
    NOT_RECORDED,
    CallLog,
    bind_calls,
    measured,
)
from gideon.integrations.llm.events import EVENT_COMPLETE, EVENT_TEXT_CHUNK
from gideon.integrations.llm.events import AgentEvent as LLMEvent
from gideon.security.guardrails.model_call import _workflow_stream_observation


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_real_stream_events_measure_complete_and_cut_off_calls() -> None:
    calls = CallLog()
    with bind_calls(calls):
        _workflow_stream_observation(
            "start", "first", provider="provider-a", model="model-a"
        )
        _workflow_stream_observation(
            "event", "first", event=LLMEvent(kind=EVENT_TEXT_CHUNK, text="working")
        )
        _workflow_stream_observation(
            "event",
            "first",
            event=LLMEvent(
                kind=EVENT_COMPLETE,
                input_tokens=7,
                output_tokens=5,
                cost_usd=0.025,
                tool_meta={"usage_reported": True},
            ),
            cost_usd=0.025,
            cost_reported=True,
        )
        _workflow_stream_observation("end", "first", completed=True)
        _workflow_stream_observation(
            "start", "second", provider="provider-b", model="model-b"
        )
        _workflow_stream_observation(
            "event",
            "second",
            event=LLMEvent(kind=EVENT_TEXT_CHUNK, text="still working"),
        )
        _workflow_stream_observation("end", "second", completed=False)

    usage = measured(calls)
    assert usage.tokens == 12
    assert usage.cost_usd == 0.025
    assert usage.calls_cut_off == 1
    assert usage.fields()["model_calls_open"] == 1
    assert usage.model == "model-a, model-b"
    assert usage.provider == "provider-a, provider-b"


def test_zero_usage_is_distinct_from_an_unreported_completed_call() -> None:
    assert NOT_RECORDED.fields()["model_calls_open"] is None
    zero = CallLog()
    with bind_calls(zero):
        _workflow_stream_observation(
            "start", "zero", provider="provider", model="model"
        )
        _workflow_stream_observation(
            "event",
            "zero",
            event=LLMEvent(kind=EVENT_COMPLETE, tool_meta={"usage_reported": True}),
            cost_usd=0.0,
        )
        _workflow_stream_observation("end", "zero", completed=True)
    assert measured(zero).tokens == 0
    assert measured(zero).cost_usd == 0.0

    unknown = CallLog()
    with bind_calls(unknown):
        _workflow_stream_observation(
            "start", "unknown", provider="provider", model="model"
        )
        _workflow_stream_observation(
            "event", "unknown", event=LLMEvent(kind=EVENT_COMPLETE)
        )
        _workflow_stream_observation("end", "unknown", completed=True)
    assert measured(unknown).tokens is None
    assert measured(unknown).cost_usd is None


def test_terminal_attempt_usage_is_written_and_read_by_the_real_status_consumer(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    run = store.create(
        WorkflowRun(id="", workflow_name="usage-run", status=RunStatus.FAILED)
    )
    spec = {
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [{"kind": "transform", "id": "work", "config": {"expr": 1}}],
        }
    }
    store.write_spec(run.id, spec)
    failed_usage = measured(CallLog(), estimate=7)
    completed_usage = measured(CallLog(), estimate=3)
    journal = Journal(run.id)
    journal.step_failed(
        "root.children[0]",
        "work",
        epoch=0,
        failure=Failure(
            failure_class=FailureClass.NETWORK, cause_plain="connection refused"
        ),
        attempt=1,
        usage=failed_usage,
    )
    journal.step_completed(
        "root.children[0]",
        "work",
        epoch=0,
        cache_key="",
        state=InstanceState.DONE,
        tokens=3,
        usage=completed_usage,
    )
    journal.step_cancelled(
        "root.children[0]",
        "work",
        epoch=0,
        attempt=3,
        usage=measured(CallLog(), estimate=2),
    )
    store.write_state(
        run.id,
        {
            "root.children[0]": NodeInstance(
                path="root.children[0]",
                state=InstanceState.FAILED,
                attempt=3,
                failure=Failure(failure_class=FailureClass.NETWORK),
            )
        },
    )

    rows = status(run.id)["nodes"][0]["attempts"]
    assert [row["kind"] for row in rows] == [
        "step_failed",
        "step_completed",
        "step_cancelled",
    ]
    assert [row["tokens"] for row in rows] == [7, 3, 2]
    inspected = inspect_node(run.id, "work")["attempts"]
    assert [row["kind"] for row in inspected] == [
        "step_failed",
        "step_completed",
        "step_cancelled",
    ]
    assert [row["tokens"] for row in inspected] == [7, 3, 2]
    assert journal_mod.run_totals(run.id)["tokens"] == 10


@pytest.mark.anyio
async def test_real_failed_transform_attempt_records_zero_as_measured_usage(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    spec = {
        "name": "missing-binding",
        "root": {
            "kind": "sequence",
            "id": "root",
            "children": [
                {
                    "kind": "transform",
                    "id": "work",
                    "config": {"expr": "{{nodes.missing.output}}"},
                }
            ],
        },
    }
    run = store.create(WorkflowRun(id="", workflow_name="missing-binding"))
    store.write_spec(run.id, spec)
    controller = RunController(run, spec)

    assert await controller.run_to_completion(timeout=5) is RunStatus.FAILED
    rows = status(run.id)["nodes"][0]["attempts"]
    assert len(rows) == 1
    assert rows[0]["kind"] == "step_failed"
    assert rows[0]["tokens"] == 0
    assert rows[0]["cost_usd"] == 0.0
