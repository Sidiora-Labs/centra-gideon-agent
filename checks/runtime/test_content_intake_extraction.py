import asyncio
import os
import tempfile
import threading
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from docx import Document
from reportlab.pdfgen.canvas import Canvas

from gideon.cognition.knowledge.extract import extract_file_content
from gideon.cognition.knowledge.pipeline import ensure_nodes_registered, graph_for
from gideon.cognition.knowledge.pipeline.executor import PipelineExecutor
from gideon.cognition.knowledge.pipeline.graph import NodeSpec, PipelineGraph
from gideon.cognition.knowledge.pipeline.registry import NODE_REGISTRY
from gideon.cognition.knowledge.pipeline.types import NodeContext, NodeOutput
from gideon.cognition.knowledge.readers import FileReader
from gideon.interfaces.dashboard import attachment_extract
from gideon.interfaces.dashboard.handlers.files import api_attachment_extract
from gideon.workspace.uploads.content_intake import (
    SCAN_WINDOW,
    ApprovedFile,
    IntakeRefused,
    approve_path,
    approve_text,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def test_real_compressed_docx_scan_blocks_output_and_dependencies(isolated):
    path = isolated / "untrusted.docx"
    document = Document()
    document.add_paragraph("safe\u202ereversed\u202c")
    document.save(path)

    async def check():
        ensure_nodes_registered()
        result = await PipelineExecutor(graph_for("document")).run(
            NodeContext(item_id="doc", item_type="document", file_path=str(path))
        )
        assert "document_read" in result.failed
        assert (
            result.outputs["document_read"].metadata["content_refusal"]
            == "upload_content_refused"
        )
        assert not result.pooled_outputs()
        assert "consolidate" not in result.ran
        assert result.outcomes["document_read"].status == "failed"
        assert result.outcomes["consolidate"].status == "skipped"
        with pytest.raises(IntakeRefused):
            await extract_file_content(str(path))
        assert not list(isolated.glob("gideon-reader-*"))
        assert not list(isolated.glob("gideon-intake-*"))

    asyncio.run(check())


def test_model_facing_unicode_prefix_stays_inside_scanned_bytes(isolated):
    async def check():
        original = "界" * 200000
        approved = await approve_text(original, surface="test")
        assert approved.text == original
        assert len(approved.scanned_head.encode("utf-8")) <= SCAN_WINDOW
        assert original.startswith(approved.scanned_head)

    asyncio.run(check())


def test_real_reader_uses_original_snapshot_and_source_change_is_withheld(
    isolated, monkeypatch
):
    path = isolated / "source.txt"
    path.write_text("Original source text.")

    async def check():
        snapshot = await approve_path(path, surface="test")
        path.write_text("Replacement source text.")
        try:
            text, metadata = await asyncio.to_thread(
                FileReader().read_approved, snapshot
            )
            assert text == "Original source text."
            assert metadata["source_digest"] == snapshot.digest
        finally:
            snapshot.close()
            snapshot.close()  # Closing a completed lifetime cannot close a reused descriptor.
        original = FileReader.read_approved

        def read_then_change(self, approved):
            text, metadata = original(self, approved)
            path.write_text("Changed during real extraction.")
            return text, metadata

        monkeypatch.setattr(FileReader, "read_approved", read_then_change)
        with pytest.raises(IntakeRefused) as changed:
            await extract_file_content(str(path))
        assert changed.value.code == "upload_content_changed"
        assert not list(isolated.glob("gideon-reader-*"))
        assert not list(isolated.glob("gideon-intake-*"))

    asyncio.run(check())


def test_cache_refresh_and_real_attachment_api_structured_denial(isolated, monkeypatch):
    root = isolated / "home" / "uploads"
    root.mkdir(parents=True)
    path = root / "readme.txt"
    path.write_text("First source text.")
    extractor = attachment_extract.AttachmentExtractor()
    monkeypatch.setattr(attachment_extract, "_INSTANCE", extractor)

    async def check():
        app = web.Application()
        app.router.add_get("/api/attachment-extract", api_attachment_extract)
        async with TestClient(TestServer(app)) as client:
            assert await extractor.get(str(path), strict=True) == "First source text."
            path.write_text("New source text, not the cached result.")
            assert (
                await extractor.get(str(path), strict=True)
                == "New source text, not the cached result."
            )
            path.write_text("safe\u202eevil\u202c")
            response = await client.get(
                "/api/attachment-extract", params={"path": str(path)}
            )
            assert response.status == 422
            answer = await response.json()
            assert answer["error"]["code"] == "upload_content_refused"
            receipt = await extractor.get(str(path))
            assert "failed the safety scan" in receipt and "\u202e" not in receipt
            outside = isolated / "outside.txt"
            outside.write_text("Outside upload permission boundary.")
            response = await client.get(
                "/api/attachment-extract", params={"path": str(outside)}
            )
            assert response.status == 403

    asyncio.run(check())


def test_actual_reader_invalid_file_and_missing_ocr_are_not_success(isolated):
    invalid = isolated / "broken.docx"
    invalid.write_bytes(b"Not a document container.")
    pdf = isolated / "scanned.pdf"
    canvas = Canvas(str(pdf))
    canvas.rect(0, 0, 300, 300, fill=1)
    canvas.save()

    async def check():
        ensure_nodes_registered()
        for path in (invalid, pdf):
            result = await PipelineExecutor(graph_for("document")).run(
                NodeContext(
                    item_id=path.name, item_type="document", file_path=str(path)
                )
            )
            assert "document_read" in result.failed
            assert not result.outputs["document_read"].success
            assert not result.pooled_outputs()
        assert not list(isolated.glob("gideon-reader-*"))

    asyncio.run(check())


def test_native_executor_gates_secondary_text_and_preserves_owner_note(
    isolated, monkeypatch
):
    called = []

    class ExternalNode:
        node_type = "external_text"
        backend = "test"
        uses_use_case = None

        async def run(self, inputs, ctx):
            return NodeOutput(
                node_type=self.node_type,
                text="Ordinary primary text.",
                segments=[{"text": "hidden\u202epayload\u202c"}],
            )

    class ConsumerNode:
        node_type = "text_consumer"
        backend = "test"
        uses_use_case = None

        async def run(self, inputs, ctx):
            called.append(inputs)
            return NodeOutput(node_type=self.node_type, text="Consumed")

    monkeypatch.setitem(NODE_REGISTRY, ("external_text", "test"), ExternalNode())
    monkeypatch.setitem(NODE_REGISTRY, ("text_consumer", "test"), ConsumerNode())
    graph = PipelineGraph(item_type="document")
    graph.add(NodeSpec(node_type="external_text", backend="test"))
    graph.add(NodeSpec(node_type="text_consumer", backend="test"))
    graph.edge("external_text", "text_consumer")

    async def check():
        result = await PipelineExecutor(graph).run(
            NodeContext(
                item_id="external",
                item_type="document",
                params={"_content_scan_required": True},
            )
        )
        assert "external_text" in result.failed and not called
        assert result.outcomes["text_consumer"].status == "skipped"
        ensure_nodes_registered()
        owner_text = "Owner-authored\u202e note\u202c"
        owner = await PipelineExecutor(graph_for("note")).run(
            NodeContext(item_id="owner", item_type="note", content=owner_text)
        )
        assert (
            owner.outputs["passthrough"].text == owner_text and owner.status == "done"
        )

    asyncio.run(check())


def test_approval_does_not_grant_sensitive_or_symlink_sources(isolated):
    path = isolated / "permitted.txt"
    path.write_text("Ordinary source")
    linked = isolated / "symlink.txt"
    linked.symlink_to(path)

    async def check():
        with pytest.raises(PermissionError):
            await approve_path("/root/.ssh/id_rsa", surface="test")
        with pytest.raises(PermissionError):
            await extract_file_content("/root/.ssh/id_rsa")
        with pytest.raises(OSError):
            await approve_path(linked, surface="test")
        fd = os.open(path, os.O_RDONLY)
        forged = ApprovedFile(
            "permitted.txt", None, path.stat().st_size, "", "test", fd
        )
        try:
            with pytest.raises(PermissionError):
                FileReader().read_approved(forged)
        finally:
            forged.close()

    asyncio.run(check())


def test_real_inflight_cache_change_withholds_prior_read(isolated, monkeypatch):
    path = isolated / "changing.txt"
    path.write_text("First source text.")
    entered, release = threading.Event(), threading.Event()
    original = FileReader.read_approved
    blocked = False

    def read_with_barrier(self, snapshot):
        nonlocal blocked
        result = original(self, snapshot)
        if not blocked:
            blocked = True
            entered.set()
            assert release.wait(15)
        return result

    monkeypatch.setattr(FileReader, "read_approved", read_with_barrier)
    extractor = attachment_extract.AttachmentExtractor()

    async def check():
        prior = asyncio.create_task(extractor.get(str(path), strict=True))
        try:
            assert await asyncio.to_thread(entered.wait, 15)
            path.write_text("Second source text.")
            current = asyncio.create_task(extractor.get(str(path), strict=True))
            await asyncio.sleep(0.05)
            release.set()
            with pytest.raises(IntakeRefused) as refused:
                await prior
            assert refused.value.code == "upload_content_changed"
            assert await current == "Second source text."
            assert not list(isolated.glob("gideon-reader-*"))
        finally:
            release.set()
            if not prior.done():
                prior.cancel()
                await asyncio.gather(prior, return_exceptions=True)

    asyncio.run(check())
