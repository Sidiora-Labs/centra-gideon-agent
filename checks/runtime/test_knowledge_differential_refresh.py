import json

import pytest

from gideon.cognition.knowledge.chunking import chunk_text, content_digest
from gideon.cognition.knowledge.embedder import UnifiedEmbedder, floats_to_bytes
from gideon.cognition.knowledge.pipeline.runner import embed_item_chunks
from gideon.cognition.knowledge.store import KnowledgeStore


@pytest.fixture
def store(tmp_path):
    instance = KnowledgeStore(str(tmp_path / "knowledge.db"))
    yield instance
    instance.close()


def seed(store, content, *, provider="local", model="encoder-v1"):
    item_id = store.create_typed_item(
        item_type="document", title="Notes", content=content
    )
    chunks = chunk_text(content)
    for index, chunk in enumerate(chunks):
        chunk.embedding = floats_to_bytes([float(index + 1), 0.5, 0.25])
        chunk.embedding_provider = provider
        chunk.embedding_model = model
    store.replace_chunks(item_id, chunks)
    return item_id, chunks


def unavailable_embedder(provider="local", model="encoder-v1"):
    embedder = UnifiedEmbedder(None, dim_hint=3)
    embedder.embedding_provider = provider
    embedder.embedding_model = model
    return embedder


def test_unchanged_vectors_survive_unavailable_provider_and_reopen(store):
    content = "# Alpha\nfirst\n# Beta\nsecond\n"
    item_id, chunks = seed(store, content)
    reopened = KnowledgeStore(store.db_path)
    try:
        embed_item_chunks(reopened, item_id, content, unavailable_embedder())
        rows = reopened.get_chunks(item_id, with_embedding=True)
        assert [row["embedding"] for row in rows] == [
            [1.0, 0.5, 0.25],
            [2.0, 0.5, 0.25],
        ]
        assert [row["section_digest"] for row in rows] == [
            chunk.section_digest for chunk in chunks
        ]
        assert [json.loads(row["section_key"]) for row in rows] == [
            ["Alpha", 0],
            ["Beta", 0],
        ]
    finally:
        reopened.close()


def test_changed_section_invalidated_unchanged_section_relocated(store):
    item_id, _ = seed(store, "# Alpha\nfirst\n# Beta\nsecond\n# Deleted\nold")
    content = "# Alpha\nchanged\nanother line\n# Beta\nsecond\n# Added\nnew"
    store.update_item(item_id, content=content)
    embed_item_chunks(store, item_id, content, unavailable_embedder())
    rows = store.get_chunks(item_id)
    assert [row["section"] for row in rows] == ["Alpha", "Beta", "Added"]
    assert [row["has_embedding"] for row in rows] == [False, True, False]
    assert rows[1]["line_start"] == 4
    assert [row["chunk_index"] for row in rows] == [0, 1, 2]


@pytest.mark.parametrize("linebreak", ["\n", "\r\n", "\r"])
def test_canonical_hashes_and_vectors_across_line_endings(store, linebreak):
    content = "# Alpha\nfirst\nsecond\n"
    equivalent = content.replace("\n", linebreak)
    assert content_digest(content) == content_digest(equivalent)
    assert content_digest("a b") != content_digest("a  b")
    item_id, original = seed(store, content)
    embed_item_chunks(store, item_id, equivalent, unavailable_embedder())
    row = store.get_chunks(item_id)[0]
    assert row["has_embedding"]
    assert row["section_digest"] == original[0].section_digest
    assert row["text"] == original[0].text


@pytest.mark.parametrize(
    "provider,model",
    [("other", "encoder-v1"), ("local", "encoder-v2"), ("", ""), ("local", "")],
)
def test_fingerprint_mismatch_or_unknown_refuses_carry(store, provider, model):
    content = "# Alpha\nfirst"
    item_id, _ = seed(store, content)
    embed_item_chunks(store, item_id, content, unavailable_embedder(provider, model))
    row = store.get_chunks(item_id)[0]
    assert not row["has_embedding"]
    assert row["embedding_provider"] == row["embedding_model"] == ""


def test_legacy_rows_without_digest_are_not_carried(store):
    content = "# Alpha\nfirst"
    item_id, _ = seed(store, content)
    store.db.execute(
        "UPDATE chunks SET section_digest = '' WHERE item_id = ?", (item_id,)
    )
    embed_item_chunks(store, item_id, content, unavailable_embedder())
    row = store.get_chunks(item_id)[0]
    assert not row["has_embedding"]
    assert len(row["section_digest"]) == 64


def test_duplicate_headings_and_changed_long_section(store):
    body = "\n".join("line " + str(index) + " x" * 50 for index in range(45))
    content = "# Same\nfirst\n# Same\n" + body + "\n# Last\nretained"
    item_id, _ = seed(store, content)
    embed_item_chunks(
        store, item_id, content.replace("line 44", "edited 44"), unavailable_embedder()
    )
    rows = store.get_chunks(item_id)
    assert rows[0]["has_embedding"] and rows[-1]["has_embedding"]
    assert len(rows[1:-1]) > 1
    assert all(not row["has_embedding"] for row in rows[1:-1])
    assert json.loads(rows[0]["section_key"]) == ["Same", 0]
    assert all(json.loads(row["section_key"]) == ["Same", 1] for row in rows[1:-1])


def test_removed_content_removes_vectors(store):
    item_id, _ = seed(store, "# Alpha\nfirst")
    embed_item_chunks(store, item_id, "", unavailable_embedder())
    assert store.get_chunks(item_id) == []
