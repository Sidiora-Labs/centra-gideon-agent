"""Real SQLite memory regression for same-width embedding rebinding."""

from __future__ import annotations

from gideon.cognition.vector_memory import SemanticArchive
from gideon.extensions.providers.use_cases import save_active_models


def test_same_width_rebind_excludes_old_vectors_and_keeps_keyword_recall(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    store = SemanticArchive(db_path=home / "memory.db", embedding_dim=4)
    store.init()
    try:
        save_active_models({"embedding": ["native:model-a"]})
        assert store.write_episodic(
            "The osprey nests beside the lake.",
            embedding=[1.0, 0.0, 0.0, 0.0],
            conversation_id="before-binding-change",
        )
        old_id = store.db.execute(
            "SELECT id FROM episodic_memories WHERE conversation_id = ?",
            ("before-binding-change",),
        ).fetchone()["id"]

        save_active_models({"embedding": ["native:model-b"]})
        assert store.write_episodic(
            "A kestrel hovers above the field.",
            embedding=[0.0, 1.0, 0.0, 0.0],
            conversation_id="after-binding-change",
        )
        current_id = store.db.execute(
            "SELECT id FROM episodic_memories WHERE conversation_id = ?",
            ("after-binding-change",),
        ).fetchone()["id"]

        rows = store.search_episodic(
            query_embedding=[0.0, 1.0, 0.0, 0.0],
            query_text="osprey",
            limit=5,
            mmr=False,
        )
        by_id = {row["id"]: row for row in rows}
        assert current_id in by_id and by_id[current_id]["cosine_sim"] == 1.0
        assert old_id in by_id and "cosine_sim" not in by_id[old_id]

        save_active_models({"embedding": []})
        keyword_rows = store.vector_query(text="osprey", k=5)
        assert old_id in {row["id"] for row in keyword_rows}
        assert all("cosine_sim" not in row for row in keyword_rows)
    finally:
        store.close()
