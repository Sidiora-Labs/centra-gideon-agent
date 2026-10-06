from __future__ import annotations

import json

import pytest

from gideon.cognition.knowledge.pipeline import ensure_nodes_registered, graph_for
from gideon.cognition.knowledge.pipeline import outcomes as oc
from gideon.cognition.knowledge.pipeline.executor import PipelineExecutor
from gideon.cognition.knowledge.pipeline.graph import NodeSpec, PipelineGraph
from gideon.cognition.knowledge.pipeline.registry import unserved_reason_sync
from gideon.cognition.knowledge.pipeline.types import NodeContext


@pytest.fixture(autouse=True)
def real_nodes():
    ensure_nodes_registered()


@pytest.mark.asyncio
async def test_actual_text_ingestion_done_outcome_survives_serialization():
    result = await PipelineExecutor(graph_for("note")).run(
        NodeContext(
            item_id="text-fixture", item_type="text", content="A searchable fact."
        )
    )
    assert result.status == "done"
    assert result.outputs["passthrough"].text == "A searchable fact."
    assert json.loads(
        json.dumps({key: value.to_dict() for key, value in result.outcomes.items()})
    ) == {"passthrough": {"status": "done"}}


@pytest.mark.asyncio
async def test_untaken_actual_text_branch_is_not_a_partial_failure():
    graph = PipelineGraph(item_type="text")
    graph.add(NodeSpec("passthrough", backend="native"))
    graph.add(NodeSpec("document_slice", backend="native"))
    graph.edge("passthrough", "document_slice", when="paper-sections")
    result = await PipelineExecutor(graph).run(
        NodeContext(item_id="branch-fixture", item_type="text", content="Plain text.")
    )
    assert result.status == "done"
    assert result.skipped == []
    assert result.not_taken == ["document_slice"]
    assert result.outcomes["document_slice"].status == oc.NOT_APPLICABLE
    assert "doesn't need" in result.outcomes["document_slice"].reason


@pytest.mark.asyncio
async def test_actual_missing_model_skip_carries_setup_to_blocked_descendant(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "active_models.json").write_text(
        json.dumps({"image_modality": ["missing-provider:missing-model"]})
    )
    graph = PipelineGraph(item_type="image")
    graph.add(NodeSpec("ocr", backend="vision-llm", uses_use_case="image_modality"))
    graph.add(NodeSpec("consolidate", backend="concat"))
    graph.edge("ocr", "consolidate")
    result = await PipelineExecutor(graph).run(
        NodeContext(item_id="unserved-fixture", item_type="image")
    )
    assert result.status == "failed"
    source, waiting = result.outcomes["ocr"], result.outcomes["consolidate"]
    assert source.status == waiting.status == oc.SKIPPED
    assert source.needs == waiting.needs == ("image_modality",)
    assert source.fix == waiting.fix
    assert source.fix[0].href == "#/settings/models"
    assert "Image · Modality" in source.fix[0].text
    assert "OCR" in waiting.reason
    assert unserved_reason_sync("image_modality")


@pytest.mark.asyncio
async def test_missing_registered_backend_and_disabled_step_have_distinct_reasons():
    missing_graph = PipelineGraph(item_type="text")
    missing_graph.add(NodeSpec("passthrough", backend="uninstalled-backend"))
    missing = await PipelineExecutor(missing_graph).run(
        NodeContext(item_id="missing-fixture", item_type="text")
    )
    disabled = await PipelineExecutor(
        graph_for("note"), params_for=lambda _: {"enabled": False}
    ).run(NodeContext(item_id="disabled-fixture", item_type="text"))
    assert (
        missing.outcomes["passthrough"].status
        == disabled.outcomes["passthrough"].status
        == oc.SKIPPED
    )
    assert (
        missing.outcomes["passthrough"].reason
        != disabled.outcomes["passthrough"].reason
    )
    assert missing.outcomes["passthrough"].needs == ()


@pytest.mark.asyncio
async def test_real_document_read_failure_propagates_as_failure_not_unneeded(tmp_path):
    graph = PipelineGraph(item_type="document")
    graph.add(NodeSpec("document_read", backend="native"))
    graph.add(NodeSpec("consolidate", backend="concat"))
    graph.edge("document_read", "consolidate")
    result = await PipelineExecutor(graph).run(
        NodeContext(
            item_id="unreadable-fixture",
            item_type="document",
            file_path=str(tmp_path / "missing.txt"),
        )
    )
    assert result.outcomes["document_read"].status == oc.FAILED
    assert result.outcomes["document_read"].reason
    assert result.outcomes["consolidate"].status == oc.SKIPPED
    assert "failed" in result.outcomes["consolidate"].reason


@pytest.mark.asyncio
async def test_actual_timeout_records_budget_in_failure_reason():
    result = await PipelineExecutor(
        graph_for("note"), params_for=lambda _: {"timeout_s": 0}
    ).run(
        NodeContext(
            item_id="timeout-fixture",
            item_type="text",
            content="Cannot run before cancellation.",
        )
    )
    assert result.outcomes["passthrough"].status == oc.FAILED
    assert "0 seconds" in result.outcomes["passthrough"].reason


@pytest.mark.asyncio
async def test_actual_bounded_loop_replaces_previous_iteration_outcomes():
    graph = PipelineGraph(item_type="text")
    graph.add(NodeSpec("passthrough", backend="native"))
    graph.add(NodeSpec("consolidate", backend="concat"))
    graph.edge("passthrough", "consolidate")
    graph.loop_edge("consolidate", "passthrough", when="", max_iters=2)
    events = []
    result = await PipelineExecutor(
        graph, on_node=lambda node, phase: events.append((node, phase))
    ).run(
        NodeContext(
            item_id="loop-fixture", item_type="text", content="Looped actual text."
        )
    )
    assert events.count(("passthrough", "done")) == 3
    assert result.ran == ["passthrough", "consolidate"]
    assert all(outcome.status == oc.DONE for outcome in result.outcomes.values())
    assert result.status == "done"


def test_legacy_skip_is_not_given_a_fabricated_reason_or_provider_requirement():
    assert oc.legacy("skipped") == {
        "status": "skipped",
        "reason": oc.SKIPPED_BEFORE_REASONS,
    }
    assert oc.told(
        {"ocr": oc.no_model("image_modality", "No image model is ready.").to_dict()}
    ) == [
        "OCR skipped: No image model is ready. Fix: Choose a model for Image · Modality in Settings → Models."
    ]
