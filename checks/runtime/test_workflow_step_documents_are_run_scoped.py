"""Step documents are retained under their run identity, even on a shared workspace."""

from __future__ import annotations

import asyncio
import time

from gideon.automation.workflows import deliverable, service, store
from gideon.automation.workflows.controller import (
    EngineServices,
    NodeResult,
    RunController,
    _InFlight,
)
from gideon.automation.workflows.journal import CacheKey
from gideon.automation.workflows.models import InstanceState, RunStatus, WorkflowRun
from gideon.automation.workflows.step_usage import CallLog
from gideon.automation.workflows.tick import ReadyNode


async def _settle_document(run: WorkflowRun, workspace, name: str, body: str, step: str):
    spec = {"root": {"kind": "transform", "id": step, "config": {"expr": "'done'"}}}
    store.write_spec(run.id, spec)
    controller = RunController(run, spec, services=EngineServices(cwd=str(workspace)))
    before = deliverable.step_document_snapshot(workspace)
    (workspace / name).write_text(body, encoding="utf-8")
    node = controller.root
    ready = ReadyNode(path=step, node=node, lane=node.lane)
    entry = _InFlight(
        task=asyncio.create_task(asyncio.sleep(0)),
        ready=ready,
        started=time.time(),
        last_progress=time.time(),
        cache_key=CacheKey(step, 0, "", ""),
        calls=CallLog(),
        document_root=str(workspace),
        document_snapshot=before,
    )
    controller._apply(entry, NodeResult(state=InstanceState.DONE, output="done"))
    await entry.task


def _new_run(workspace) -> WorkflowRun:
    run = store.create(WorkflowRun(id="", workflow_name="goal-pursuit-open-ended", status=RunStatus.RUNNING))
    run.extra["workspace"] = {"path": str(workspace), "isolated": False}
    store.save(run)
    return run


async def test_shared_workspace_documents_are_retained_per_run(monkeypatch, tmp_path):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    shared = tmp_path / "shared"
    shared.mkdir()
    run_a = _new_run(shared)
    run_b = _new_run(shared)

    (shared / "REPORT.md").write_text("baseline", encoding="utf-8")
    (shared / "outside.md").symlink_to(tmp_path / "outside.md")
    (shared / "oversized.md").write_bytes(b"x" * (deliverable.MAX_DOC_BYTES + 1))
    await _settle_document(
        run_a,
        shared,
        "REPORT.md",
        "Run A first report",
        "first",
    )
    await _settle_document(run_b, shared, "REPORT.md", "Run B report", "write")
    await _settle_document(
        run_a,
        shared,
        "REPORT.md",
        "Run A replacement\napi_key=sk-sensitive-value",
        "revise",
    )

    a = service.run_deliverable(run_a.id)
    b = service.run_deliverable(run_b.id)
    assert a["ok"] and b["ok"]
    assert a["report"]["content"].startswith("Run A replacement\n")
    assert "sk-sensitive-value" not in a["report"]["content"]
    assert b["report"]["content"] == "Run B report"
    assert [doc["name"] for doc in a["step_documents"]] == ["REPORT.md"]
    assert [doc["name"] for doc in b["step_documents"]] == ["REPORT.md"]
    assert "sensitive-value" not in a["step_documents"][0]["content"]
    assert not (store.run_dir(run_a.id) / deliverable.STEP_DOCUMENTS_DIR / "outside.md").exists()
    assert not (store.run_dir(run_a.id) / deliverable.STEP_DOCUMENTS_DIR / "oversized.md").exists()
