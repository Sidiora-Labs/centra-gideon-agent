"""KL-9 — structural chunker + additive chunk embedding in ingest.

Covers the chunker's boundary strategy (markdown headings, pptx slides, xlsx sheets,
structureless size fallback + overlap, over-long single section) with section/line-range
assertions, and the ingest wiring (chunks embedded + the item's whole-item embedding
retained). All SQLite state is isolated under ``tmp_path`` and connections are closed.
"""

import asyncio
import struct

import pytest

from gideon.cognition.knowledge.chunking import MAX_CHARS, OVERLAP, chunk_text
from gideon.cognition.knowledge.pipeline import ensure_nodes_registered
from gideon.cognition.knowledge.pipeline.runner import ingest_item
from gideon.cognition.knowledge.store import KnowledgeStore


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture
def store(tmp_path):
    s = KnowledgeStore(str(tmp_path / "k.db"))
    try:
        yield s
    finally:
        s.close()


class _CharEmbedder:
    """A deterministic embedder that maps any text to a fixed-dim vector, so both the
    whole-item and chunk embeddings are exercised without a model. ``embed_for_item``
    (whole item) and ``embed`` (per chunk) are both provided."""

    def is_available(self):
        return True

    def embed(self, text):
        if not (text or "").strip():
            return None
        t = text.strip()
        return [float(len(t)), float(ord(t[0])), float(ord(t[-1]))]

    def embed_for_item(self, title, summary, content=None):
        from gideon.cognition.knowledge.embedder import compose_item_text

        return self.embed(compose_item_text(title, summary, content))


def test_markdown_headings_become_sections_with_line_ranges():
    content = (
        "Intro line one\n"
        "intro line two\n"
        "# Alpha\n"
        "alpha body\n"
        "## Beta\n"
        "beta body line\n"
        "more beta\n"
    )
    chunks = chunk_text(content)
    sections = [c.section for c in chunks]
    assert sections == [None, "Alpha", "Beta"]
    assert [c.chunk_index for c in chunks] == [0, 1, 2]
    assert (chunks[0].line_start, chunks[0].line_end) == (1, 2)
    assert chunks[1].line_start == 3 and chunks[1].text.startswith("# Alpha")
    assert (chunks[2].line_start, chunks[2].line_end) == (5, 7)
    assert "beta body line" in chunks[2].text and "more beta" in chunks[2].text


def test_pptx_style_slides_chunk_per_slide():
    content = (
        "## Slide 1: Title\nfirst slide body\n"
        "\n"
        "## Slide 2: Agenda\nsecond slide body\n"
        "\n"
        "## Slide 3: Summary\nthird slide body\n"
    )
    chunks = chunk_text(content)
    assert [c.section for c in chunks] == [
        "Slide 1: Title",
        "Slide 2: Agenda",
        "Slide 3: Summary",
    ]
    assert len(chunks) == 3
    assert "first slide body" in chunks[0].text


def test_xlsx_style_sheets_chunk_per_sheet():
    content = (
        "## Q1\n| a | b |\n| --- | --- |\n| 1 | 2 |\n"
        "\n"
        "## Q2\n| c | d |\n| --- | --- |\n| 3 | 4 |\n"
    )
    chunks = chunk_text(content)
    assert [c.section for c in chunks] == ["Q1", "Q2"]


def test_structureless_blob_chunks_by_size_with_overlap():
    lines = [f"line {i:04d} " + ("x" * 60) for i in range(200)]
    content = "\n".join(lines)
    chunks = chunk_text(content)
    assert len(chunks) > 1
    assert all(c.section is None for c in chunks)
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    overlapped = any(
        chunks[i + 1].line_start <= chunks[i].line_end for i in range(len(chunks) - 1)
    )
    assert overlapped
    assert chunks[0].line_start == 1
    assert chunks[-1].line_end == len(lines)


def test_long_section_falls_back_to_size_split():
    body = "\n".join(f"paragraph {i} " + ("y" * 80) for i in range(100))
    content = f"# Big Section\n{body}\n"
    chunks = chunk_text(content)
    assert len(chunks) > 1
    assert all(c.section == "Big Section" for c in chunks)
    assert all(len(c.text) <= MAX_CHARS for c in chunks)


def test_over_long_single_line_is_windowed():
    content = "# H\n" + ("z" * (MAX_CHARS * 3))
    chunks = chunk_text(content)
    assert len(chunks) >= 3
    assert all(len(c.text) <= MAX_CHARS for c in chunks)
    body_chunks = [c for c in chunks if "z" in c.text]
    assert all(c.line_start == c.line_end == 2 for c in body_chunks)


def test_blank_document_yields_no_chunks():
    assert chunk_text("") == []
    assert chunk_text("   \n\n  \n") == []


def test_overlap_is_bounded():
    lines = [("w" * 100) for _ in range(50)]
    chunks = chunk_text("\n".join(lines), max_chars=300, overlap=OVERLAP)
    assert all(len(c.text) <= 300 for c in chunks)


def _raw_embedding(store, item_id):
    row = store.db.execute(
        "SELECT embedding FROM items WHERE id = ?", (item_id,)
    ).fetchone()
    return row["embedding"] if row else None


def test_ingest_embeds_chunks_and_keeps_item_embedding(store):
    ensure_nodes_registered()
    content = (
        "# Overview\n" + ("intro paragraph. " * 30) + "\n"
        "# Deep Section\n"
        "The secret answer is quantum-dot-photovoltaics-42.\n"
        + ("filler. " * 40)
        + "\n"
    )
    item_id = store.create_typed_item(
        item_type="document", title="Long Doc", content=content, summary="a doc"
    )
    _run(ingest_item(store, item_id, embedder=_CharEmbedder()))

    assert store.get_item(item_id).get("has_embedding")
    assert _raw_embedding(store, item_id) is not None

    chunks = store.get_chunks(item_id)
    assert len(chunks) >= 2
    assert all(c["has_embedding"] for c in chunks)
    assert any(c["section"] == "Deep Section" for c in chunks)
    deep = next(c for c in chunks if c["section"] == "Deep Section")
    assert deep["line_start"] and deep["line_end"] >= deep["line_start"]

    with_vec = store.get_chunks(item_id, with_embedding=True)
    assert all(isinstance(c["embedding"], list) for c in with_vec)
    raw = store.db.execute(
        "SELECT embedding FROM chunks WHERE item_id = ? ORDER BY chunk_index LIMIT 1",
        (item_id,),
    ).fetchone()["embedding"]
    assert len(struct.unpack(f"{len(raw) // 4}f", raw)) == 3


def test_reingest_replaces_chunks_not_accumulate(store):
    ensure_nodes_registered()
    item_id = store.create_typed_item(
        item_type="document",
        title="Doc",
        content="# One\nbody one\n# Two\nbody two\n",
        summary="s",
    )
    _run(ingest_item(store, item_id, embedder=_CharEmbedder()))
    first = len(store.get_chunks(item_id))
    assert first >= 2
    _run(ingest_item(store, item_id, embedder=_CharEmbedder()))
    assert len(store.get_chunks(item_id)) == first


def test_no_embedder_writes_no_chunks(store):
    ensure_nodes_registered()
    item_id = store.create_typed_item(
        item_type="document", title="Doc", content="# H\nbody\n", summary="s"
    )
    _run(ingest_item(store, item_id, embedder=None))
    assert store.get_chunks(item_id) == []
    assert not store.get_item(item_id).get("has_embedding")


def test_delete_item_cascades_chunks(store):
    ensure_nodes_registered()
    item_id = store.create_typed_item(
        item_type="document", title="Doc", content="# H\nbody line\n", summary="s"
    )
    _run(ingest_item(store, item_id, embedder=_CharEmbedder()))
    assert store.get_chunks(item_id)
    store.delete_item(item_id)
    rows = store.db.execute(
        "SELECT count(*) AS n FROM chunks WHERE item_id = ?", (item_id,)
    ).fetchone()
    assert rows["n"] == 0
