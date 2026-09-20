import sqlite3

from gideon.cognition.memory_linker import seed_all
from gideon.cognition.vector_memory import SemanticArchive


def test_deleted_entity_survives_rebuild_as_an_unmatchable_tombstone(tmp_path):
    knowledge_path = tmp_path / "knowledge.db"
    with sqlite3.connect(knowledge_path) as knowledge:
        knowledge.execute(
            "CREATE TABLE entities(id TEXT, name TEXT, entity_type TEXT, aliases TEXT)"
        )
        knowledge.execute(
            "INSERT INTO entities VALUES ('k1', 'Atlas Project', 'project', '[\"Atlas\"]')"
        )

    archive = SemanticArchive(db_path=tmp_path / "memory.db", embedding_dim=3)
    archive.init()
    graph = archive.graph
    assert seed_all(graph, knowledge_db_path=knowledge_path)["from_knowledge"] == 1
    entity_id = graph.entities()[0].id

    assert graph.delete_entity(entity_id)
    assert graph.entities() == []
    assert graph.entities(include_deleted=True)[0].id == entity_id

    assert seed_all(graph, knowledge_db_path=knowledge_path)["from_knowledge"] == 0
    assert graph.entities() == []
    assert graph.build_index().find("Atlas Project and Atlas") == []
    row = archive.db.execute(
        "SELECT id, is_deleted FROM mem_entities WHERE id = ?", (entity_id,)
    ).fetchone()
    assert row["is_deleted"] == 1
