import asyncio
import json
import os
import tempfile
from pathlib import Path

from gideon.cognition.archive_recall import read, search
from gideon.cognition.history import ConversationLog, HistoryConsolidator
from gideon.cognition.knowledge.readers import FileReader
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.onboarding_import import run_import, scan_source
from gideon.cognition.vector_memory import SemanticArchive
from gideon.engine.subagent import SubagentInfo
from gideon.engine.subagent_memory import capture
from reportlab.pdfgen.canvas import Canvas


with tempfile.TemporaryDirectory() as scratch:
    root = Path(scratch)
    os.environ["GIDEON_HOME"] = str(root / "gideon")
    source = root / "claude"
    project = source / "projects" / "work"
    project.mkdir(parents=True)
    events = [
        {"timestamp": "2026-01-01T10:00:00Z", "message": {"role": "user", "content": "Why did we choose blue?"}},
        {"timestamp": "2026-01-01T10:00:01Z", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "The user chose blue."},
            {"type": "tool_use", "id": "call-1", "name": "read", "input": {"path": "design.md"}},
        ]}},
        {"timestamp": "2026-01-01T10:00:02Z", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call-1", "content": "Decision: blue means calm."}
        ]}},
    ]
    (project / "chat.jsonl").write_text("\n".join(json.dumps(row) for row in events) + "\n")
    scanned = scan_source("claude_code", source)
    assert scanned.counts()["conversations"] == 1
    first = run_import([scanned], categories=["conversations"])
    assert first.counts()["imported"] == 1, first.to_dict()
    again = run_import([scanned], categories=["conversations"])
    assert again.counts()["existing"] == 1, again.to_dict()
    log = ConversationLog()
    sessions = log.list_sessions()
    assert len(sessions) == 1, sessions
    key = sessions[0]["key"]
    messages = log.read_messages(key)
    assert [row["role"] for row in messages] == ["user", "assistant", "tool"]
    assert messages[1]["tool_calls"][0]["id"] == "call-1"
    log.rewrite_session(key, [{"role": "system", "content": "summary", "ts": "2026-01-01T10:00:03Z"}], reason="compact")
    assert run_import([scanned], categories=["conversations"]).counts()["existing"] == 1
    hits = search(key, "blue")
    assert hits, hits
    recalled = read(key, hits[0]["archive"], hits[0]["index"])
    assert any("blue" in row["content"] for row in recalled["messages"]), recalled
    assert not search("missing", "blue")
    hermes = root / "hermes"
    exports = hermes / "exports"
    exports.mkdir(parents=True)
    (exports / "old-chat.jsonl").write_text("\n".join(json.dumps(message) for message in [
        {"role": "user", "content": "Summarize the plan", "timestamp": 1767261600},
        {"role": "assistant", "content": "The plan is approved", "timestamp": 1767261601},
    ]) + "\n")
    hermes_scan = scan_source("hermes", hermes)
    assert hermes_scan.counts()["conversations"] == 1
    assert run_import([hermes_scan], categories=["conversations"]).counts()["imported"] == 1

    archive = SemanticArchive(root / "memory.db")
    archive.init()
    memory = MemoryJournal(workspace=root / "workspace")
    memory.vector_store = archive
    info = SubagentInfo(id="delegate-1", task="Check the plan", result="The plan was approved",
                        done=True, agent="reviewer", parent_session_key="dashboard:main")
    receipt = capture(info, memory)
    assert receipt["status"] == "recorded", receipt
    row = archive.db.execute("SELECT text FROM episodic_memories WHERE conversation_id = ?", ("subagent:delegate-1",)).fetchone()
    assert row and "approved" in row["text"]

    pdf_path = root / "mixed.pdf"
    pdf = Canvas(str(pdf_path))
    pdf.drawString(72, 720, "The readable first page")
    pdf.showPage()
    pdf.showPage()
    pdf.save()
    body, metadata = FileReader().read(str(pdf_path))
    assert "readable first page" in body, (body, metadata)
    assert metadata["extraction_partial"] and metadata["ocr_pages_skipped"] == 1, metadata

    log.append("recoverable", "user", "Keep this observation")
    consolidator = HistoryConsolidator(log, memory)
    assert "recoverable" in consolidator._last_activity

    async def drain_extraction():
        assert consolidator._schedule.start("recoverable", True, consolidator._history_consolidated, 1)
        await consolidator.drain(timeout=1.0)
        assert not consolidator._tasks

    asyncio.run(drain_extraction())
    entity_id = archive.graph.upsert_entity("Retired Project", "project")
    assert archive.graph.delete_entity(entity_id)
    assert not any(entity.id == entity_id for entity in archive.graph.entities())
    archive.close()
    reopened = SemanticArchive(root / "memory.db")
    reopened.init()
    assert any(entity.id == entity_id for entity in reopened.graph.entities(include_deleted=True))
    try:
        reopened.graph.upsert_entity("Retired Project", "project")
    except ValueError:
        pass
    else:
        raise AssertionError("deleted entity was recreated")
    print("runtime memory import, archive recall, delegated contribution, partial OCR, extraction drain, and entity tombstone passed")
