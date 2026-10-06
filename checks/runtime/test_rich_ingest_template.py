"""Exercise the served rich-ingest template with real workflow and task stores."""

from __future__ import annotations

import json
from typing import Any

import pytest

from gideon.automation.workflows.bindings import BindingContext, resolve
from gideon.automation.workflows.bundled_defs import read_template
from gideon.automation.workflows.engine import with_declared_schema_prompt
from gideon.automation.workflows.models import Node, walk
from gideon.automation.workflows.tick import _resolve_items

TEMPLATE = "rich-ingest"

#: Each persist fan-out and the lens it reads. Named rather than derived from an id prefix, so a
#: renamed fan-out fails here instead of silently dropping out of the check.
FAN_OUTS = {
    "store-decisions": "lens-decisions",
    "store-references": "lens-references",
    "store-facts": "lens-facts",
    "store-summary": "lens-summary",
    "store-tasks": "lens-tasks",
}

GATE = "grounded-in-transcript"


def _nodes() -> dict[str, Node]:
    loaded = read_template(TEMPLATE)
    assert loaded is not None, f"{TEMPLATE} does not load"
    return {node.id: node for _path, node in walk(loaded.root) if node.id}


def _items(lens: str) -> list[dict[str, Any]]:
    return [
        {"title": f"{lens} one", "body": "said in the meeting", "evidence": "line 3"},
        {"title": f"{lens} two", "body": "also said", "evidence": "line 9"},
    ]


def _ctx(null_lenses: frozenset[str] = frozenset()) -> BindingContext:
    outputs = {
        lens: None if lens in null_lenses else {"items": _items(lens)}
        for lens in FAN_OUTS.values()
    }
    return BindingContext(
        inputs={"transcript": "…", "source_label": "arch-review"}, node_outputs=outputs
    )


def test_the_template_has_the_fan_outs_and_the_gate_this_file_checks() -> None:
    nodes = _nodes()
    assert set(FAN_OUTS) | set(FAN_OUTS.values()) | {GATE} <= set(nodes)


def test_classifier_prompt_names_all_five_declared_boolean_keys() -> None:
    classifier = _nodes()["classify"]
    schema = classifier.config["schema"]
    prompt = resolve(classifier.config["prompt"], _ctx())
    prompt = with_declared_schema_prompt(prompt, schema)

    assert "exactly these five boolean keys" in prompt
    for key in ("decisions", "references", "facts", "summary", "tasks"):
        assert f'"{key}": "boolean"' in prompt


@pytest.mark.parametrize(
    "lens,fields",
    [
        ("lens-decisions", ("title", "body", "evidence")),
        ("lens-references", ("title", "body", "evidence")),
        ("lens-facts", ("title", "body", "evidence")),
        ("lens-summary", ("title", "body", "evidence")),
        ("lens-tasks", ("title", "owner", "due")),
    ],
)
def test_each_lens_prompt_names_its_schema_fields(
    lens: str, fields: tuple[str, ...]
) -> None:
    node = _nodes()[lens]
    prompt = resolve(node.config["prompt"], _ctx())
    prompt = with_declared_schema_prompt(prompt, node.config["schema"])

    assert "return only one JSON value matching this schema" in prompt
    for field in fields:
        assert f'"{field}": "string"' in prompt


@pytest.mark.parametrize("fan_out", sorted(FAN_OUTS))
def test_each_fan_out_persists_every_item_its_lens_extracted(fan_out: str) -> None:
    items = _resolve_items(_nodes()[fan_out], _ctx())
    assert items == _items(FAN_OUTS[fan_out]), (
        f"{fan_out}'s item list resolved to {items!r} — `None` is the frontier's 'not resolvable "
        "yet', so the fan-out would never start"
    )


@pytest.mark.parametrize("fan_out", sorted(FAN_OUTS))
def test_a_lens_that_came_back_null_is_an_empty_fan_out_not_a_stall(
    fan_out: str,
) -> None:
    lens = FAN_OUTS[fan_out]
    assert _resolve_items(_nodes()[fan_out], _ctx(frozenset({lens}))) == []


def test_the_judge_gate_is_shown_both_lenses_it_judges() -> None:
    prompt = resolve(_nodes()[GATE].config["prompt"], _ctx())
    assert json.dumps(_items("lens-decisions"), ensure_ascii=False) in prompt
    assert json.dumps(_items("lens-facts"), ensure_ascii=False) in prompt


def test_the_judge_gate_is_told_a_lens_found_nothing_rather_than_failing() -> None:
    prompt = resolve(
        _nodes()[GATE].config["prompt"], _ctx(frozenset({"lens-decisions"}))
    )
    assert "Extracted decisions:\n[]\n" in prompt
    assert json.dumps(_items("lens-facts"), ensure_ascii=False) in prompt


def test_allowed_failed_lens_rehydrates_as_null_without_exposing_partial_output(
    tmp_path, monkeypatch
) -> None:
    from gideon.automation.workflows import store
    from gideon.automation.workflows.controller import RunController
    from gideon.automation.workflows.models import (
        InstanceState,
        NodeInstance,
        WorkflowRun,
    )

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))

    template = read_template(TEMPLATE)
    assert template is not None
    spec = template.to_dict()
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE))
    store.write_spec(run.id, spec)
    paths = {node.id: path for path, node in walk(template.root) if node.id}

    failed_path = paths["lens-decisions"]
    facts_path = paths["lens-facts"]
    partial_ref = store.write_output(
        run.id, failed_path, {"items": _items("partial decisions")}
    )
    facts = _items("lens-facts")
    facts_ref = store.write_output(run.id, facts_path, {"items": facts})
    store.write_state(
        run.id,
        {
            failed_path: NodeInstance(
                path=failed_path,
                state=InstanceState.FAILED,
                output_ref=partial_ref,
            ),
            facts_path: NodeInstance(
                path=facts_path,
                state=InstanceState.DEGRADED,
                output_ref=facts_ref,
            ),
        },
    )

    controller = RunController(run, spec)
    assert controller._outputs["lens-decisions"] is None
    assert controller._outputs["lens-facts"] == {"items": facts}

    prompt = resolve(
        _nodes()[GATE].config["prompt"],
        BindingContext(node_outputs=controller._outputs),
    )
    assert "Extracted decisions:\n[]\n" in prompt
    assert json.dumps(facts, ensure_ascii=False) in prompt


def _terminal_subagent_controller(
    tmp_path,
    monkeypatch,
    *,
    error: str = "",
    result: str = "",
    allow_failure: bool = True,
):
    from gideon.automation.workflows import store
    from gideon.automation.workflows.controller import (
        EngineServices,
        RunController,
    )
    from gideon.automation.workflows.models import (
        InstanceState,
        NodeInstance,
        WorkflowRun,
    )
    from gideon.engine.subagent import DelegationSupervisor, SubagentInfo

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))

    template = read_template(TEMPLATE)
    assert template is not None
    if not allow_failure:
        nodes = {node.id: node for _path, node in walk(template.root) if node.id}
        nodes["lens-decisions"].config.pop("allow_failure", None)
    spec = template.to_dict()
    run = store.create(WorkflowRun(id="", workflow_name=TEMPLATE))
    store.write_spec(run.id, spec)
    failed_path = {node.id: path for path, node in walk(template.root) if node.id}[
        "lens-decisions"
    ]
    facts_path = {node.id: path for path, node in walk(template.root) if node.id}[
        "lens-facts"
    ]
    partial = {"items": _items("partial decisions")}
    partial_ref = store.write_output(run.id, failed_path, partial)
    facts = {"items": _items("lens-facts")}
    facts_ref = store.write_output(run.id, facts_path, facts)
    subagent_id = "terminal-lens-provider-result"
    store.write_state(
        run.id,
        {
            failed_path: NodeInstance(
                path=failed_path,
                state=InstanceState.RUNNING,
                output_ref=partial_ref,
                subagent_id=subagent_id,
            ),
            facts_path: NodeInstance(
                path=facts_path,
                state=InstanceState.DONE,
                output_ref=facts_ref,
            ),
        },
    )

    subagents = DelegationSupervisor(sessions=None, ctx_builder=None)
    subagents._agents[subagent_id] = SubagentInfo(
        id=subagent_id,
        task="Extract the decisions lens",
        done=True,
        error=error,
        result=result,
    )
    controller = RunController(
        store.get(run.id), spec, services=EngineServices(subagents=subagents)
    )
    controller._outputs["lens-decisions"] = partial
    return store, run, spec, failed_path, partial, partial_ref, controller


@pytest.mark.parametrize("terminal_result", ["provider_error", "schema_mismatch"])
def test_terminal_allowed_lens_failure_publishes_null_and_keeps_diagnostics(
    tmp_path, monkeypatch, terminal_result: str
) -> None:
    from gideon.automation.workflows import store as workflow_store
    from gideon.automation.workflows.bindings import BindingContext
    from gideon.automation.workflows.controller import EngineServices, RunController
    from gideon.automation.workflows.models import FailureClass, InstanceState

    error = (
        "OpenAI returned HTTP 404 model_not_found"
        if terminal_result == "provider_error"
        else ""
    )
    result = (
        "not a declared JSON object" if terminal_result == "schema_mismatch" else ""
    )
    store, run, spec, failed_path, partial, partial_ref, controller = (
        _terminal_subagent_controller(tmp_path, monkeypatch, error=error, result=result)
    )

    controller._reconcile_dispatched_stages()

    failed = controller.instances[failed_path]
    assert failed.state == InstanceState.FAILED
    assert failed.failure is not None
    assert failed.failure.cause_plain
    assert "lens-decisions" in controller._outputs
    assert controller._outputs["lens-decisions"] is None
    assert workflow_store.read_output(run.id, failed_path) == partial
    if terminal_result == "provider_error":
        assert "HTTP 404 model_not_found" in failed.failure.cause_plain
        assert failed.output_ref == partial_ref
    else:
        assert failed.failure.failure_class == FailureClass.PROTOCOL
        assert failed.output_ref == ""

    gate_prompt = resolve(
        _nodes()[GATE].config["prompt"],
        BindingContext(node_outputs=controller._outputs),
    )
    assert "Extracted decisions:\n[]\n" in gate_prompt

    rehydrated = RunController(
        workflow_store.get(run.id), spec, services=EngineServices()
    )
    assert rehydrated._outputs["lens-decisions"] is None


def test_terminal_failure_without_allow_failure_keeps_missing_reference_error(
    tmp_path, monkeypatch
) -> None:
    from gideon.automation.workflows.bindings import BindingContext, BindingError
    from gideon.automation.workflows.models import InstanceState

    store, run, _spec, failed_path, _partial, _partial_ref, controller = (
        _terminal_subagent_controller(
            tmp_path,
            monkeypatch,
            error="OpenAI returned HTTP 404 model_not_found",
            allow_failure=False,
        )
    )

    controller._reconcile_dispatched_stages()

    failed = store.read_state(run.id)[failed_path]
    assert failed.state == InstanceState.FAILED
    assert failed.failure is not None
    assert "lens-decisions" not in controller._outputs
    with pytest.raises(BindingError, match="unresolved reference at 'lens-decisions'"):
        resolve(
            _nodes()[GATE].config["prompt"],
            BindingContext(node_outputs=controller._outputs),
        )


@pytest.mark.asyncio
async def test_store_tasks_dispatch_persists_template_title_and_body(
    tmp_path, monkeypatch
) -> None:
    from gideon.automation.workflows.engine import dispatch_action
    from gideon.automation.workflows.models import InstanceState
    from gideon.engine.tasks.registry import list_all_tasks
    from gideon.integrations.action_providers.create_task_provider import (
        CreateTaskActionProvider,
    )
    from gideon.integrations.action_providers.registry import (
        dispatchable_action_providers,
        get_action_provider,
    )

    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))

    source_label = "rich-ingest-regression-source"
    item = {"title": "Publish the revised schema", "owner": "Jordan"}
    context = BindingContext(
        inputs={"source_label": source_label}, item=item, has_item=True
    )
    action = _nodes()["create-task"]
    assert "create-task" in dispatchable_action_providers()
    assert isinstance(get_action_provider("create-task"), CreateTaskActionProvider)

    result = await dispatch_action(action, context, run_id="rich-ingest-regression")

    assert result.state == InstanceState.DONE
    tasks, total = await list_all_tasks(provider_filter="native")
    assert total == 1
    assert len(tasks) == 1
    assert tasks[0].title == item["title"]
    assert tasks[0].description == f"From {source_label}. Owner: Jordan"
