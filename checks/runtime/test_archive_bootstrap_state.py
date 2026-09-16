import json
import stat
from datetime import datetime

import pytest

from gideon.cognition import vector_memory as module
from gideon.cognition.memory_record import (
    MemoryKind,
    MemoryRecord,
    MemoryScope,
    MemoryTier,
)
from gideon.cognition.vector_memory import SemanticArchive, sqlite3
from gideon.core.config.loader import AppConfig
from gideon.integrations.memory_providers.base import MemoryProvider


@pytest.fixture
def archive_home(tmp_path, monkeypatch):
    home = tmp_path / "archive-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    config = AppConfig.load()
    config.memory.graph_enabled = False
    config.save()
    return home


@pytest.fixture
def archive(archive_home):
    store = SemanticArchive(
        db_path=archive_home / "records" / "memory.db", embedding_dim=3
    )
    store.init()
    yield store
    store.close()


def legacy_database(path):
    database = sqlite3.connect(str(path))
    database.row_factory = sqlite3.Row
    database.executescript(module._SCHEMA_V1)
    database.execute(
        "INSERT INTO schema_version VALUES (1, '2000-01-01T00:00:00+00:00')"
    )
    database.execute(
        "INSERT INTO semantic_memory VALUES ('pref.editor', '\"vim\"', 1.0, 'user_explicit', '2000-01-01', '2000-01-01', 0)"
    )
    database.execute(
        "INSERT INTO episodic_memories (id,text,created_at) VALUES ('episode-old', 'A retained old memory record.', '2000-01-01')"
    )
    database.commit()
    return database


def test_constructor_is_lazy_and_copies_configuration(archive_home):
    path = archive_home / "not-created" / "custom.db"
    prefixes = ["custom.*"]
    store = SemanticArchive(
        db_path=path,
        extra_prefixes=prefixes,
        confidence_threshold=0.6,
        dedup_threshold=0.75,
        episodic_max=9,
        embedding_dim=3,
        episodic_limit=4,
    )
    assert isinstance(store, MemoryProvider) and store.name == "native-vector"
    assert not path.parent.exists()
    assert store._db_path == path and store._faiss_path == path.parent / "memory.faiss"
    assert (
        store._confidence_threshold,
        store._dedup_threshold,
        store._episodic_max,
        store._episodic_limit,
        store._embedding_dim,
    ) == (0.6, 0.75, 9, 4, 3)
    prefixes.append("later.*")
    assert "custom.*" in store._prefixes and "later.*" not in store._prefixes
    assert store._db_lock.acquire(blocking=False)
    assert not store._db_lock.acquire(blocking=False)
    store._db_lock.release()
    assert store._faiss_id_map == [] and store._faiss_writes_since_save == 0
    assert store.embed_fn is None and store.contradiction_judge is None
    assert SemanticArchive()._db_path == archive_home / "memory.db"
    with pytest.raises(
        RuntimeError, match="SemanticArchive not initialized — call init"
    ):
        _ = store.db
    store.close()


def test_open_applies_connection_policy_permissions_and_close_guard(archive):
    connection = archive.db
    assert connection.row_factory is sqlite3.Row
    assert connection.isolation_level == ""
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert stat.S_IMODE(archive._db_path.stat().st_mode) == 0o600
    assert [
        row[0]
        for row in connection.execute(
            "SELECT version FROM schema_version ORDER BY version"
        )
    ] == list(range(1, 11))
    for row in connection.execute("SELECT applied_at FROM schema_version"):
        assert datetime.fromisoformat(row[0]).tzinfo is not None
    archive.close()
    archive.close()
    with pytest.raises(RuntimeError, match="not initialized"):
        _ = archive.db
    with pytest.raises(sqlite3.Error):
        connection.execute("SELECT 1")


def test_full_upgrade_preserves_v1_rows_and_honest_attribution(archive_home):
    path = archive_home / "old.db"
    old = legacy_database(path)
    old.close()
    store = SemanticArchive(db_path=path, embedding_dim=3)
    store.init()
    try:
        fact = store.db.execute(
            "SELECT * FROM semantic_memory WHERE key='pref.editor'"
        ).fetchone()
        assert fact["value_json"] == '"vim"' and fact["source"] == "user_explicit"
        assert (
            fact["tier"],
            fact["scope"],
            fact["contributor"],
            fact["holder"],
            fact["weight"],
        ) == ("semantic", "global", "", "", 1.0)
        assert (
            fact["embedding"],
            fact["superseded_by"],
            fact["invalidated_at"],
            fact["recall_count"],
            fact["visit_count"],
        ) == (None, None, None, 0, 0)
        episode = store.db.execute(
            "SELECT * FROM episodic_memories WHERE id='episode-old'"
        ).fetchone()
        assert (episode["tier"], episode["scope"], episode["contributor"]) == (
            "episodic",
            "global",
            "",
        )
        versions = [
            tuple(row)
            for row in store.db.execute("SELECT * FROM schema_version ORDER BY version")
        ]
    finally:
        store.close()
    reopened = SemanticArchive(db_path=path, embedding_dim=3)
    reopened.init()
    try:
        assert [
            tuple(row)
            for row in reopened.db.execute(
                "SELECT * FROM schema_version ORDER BY version"
            )
        ] == versions
        assert reopened.get("pref.editor").value == "vim"
    finally:
        reopened.close()


@pytest.mark.parametrize("version", range(2, 11))
def test_each_upgrade_tolerates_reapplication_without_losing_rows(
    archive_home, version
):
    database = legacy_database(archive_home / f"migration-{version}.db")
    try:
        for number in range(2, version + 1):
            getattr(module, f"_migrate_v{number}")(database)
        database.commit()
        before = [
            tuple(row)
            for row in database.execute(
                "SELECT name,sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY name"
            )
        ]
        getattr(module, f"_migrate_v{version}")(database)
        database.commit()
        assert [
            tuple(row)
            for row in database.execute(
                "SELECT name,sql FROM sqlite_master WHERE type IN ('table','index') ORDER BY name"
            )
        ] == before
        assert (
            database.execute("SELECT value_json FROM semantic_memory").fetchone()[0]
            == '"vim"'
        )
        assert (
            database.execute("SELECT text FROM episodic_memories").fetchone()[0]
            == "A retained old memory record."
        )
    finally:
        database.close()


def test_axis_upgrade_backfills_only_null_tiers(archive_home):
    database = legacy_database(archive_home / "tiers.db")
    try:
        module._migrate_v6(database)
        database.execute("UPDATE semantic_memory SET tier='working', scope=NULL")
        database.execute("UPDATE episodic_memories SET tier=NULL")
        module._migrate_v6(database)
        assert tuple(
            database.execute("SELECT tier,scope FROM semantic_memory").fetchone()
        ) == ("working", None)
        assert (
            database.execute("SELECT tier FROM episodic_memories").fetchone()[0]
            == "episodic"
        )
    finally:
        database.close()


def test_column_upgrade_propagates_nonduplicate_database_errors(archive_home):
    database = sqlite3.connect(str(archive_home / "missing.db"))
    try:
        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            module._migrate_v2(database)
        database.executescript(module._SCHEMA_V1)
        database.execute("PRAGMA query_only=ON")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            module._migrate_v2(database)
    finally:
        database.close()


def test_partial_holder_upgrade_reports_existing_column_and_finishes(
    archive_home, caplog
):
    database = legacy_database(archive_home / "partial.db")
    try:
        database.execute(
            "ALTER TABLE semantic_memory ADD COLUMN holder TEXT DEFAULT ''"
        )
        database.execute("UPDATE semantic_memory SET holder='user'")
        with caplog.at_level("DEBUG", logger=module.__name__):
            module._migrate_v10(database)
        assert "semantic_memory.holder already present" in caplog.text
        assert tuple(
            database.execute("SELECT holder,weight FROM semantic_memory").fetchone()
        ) == ("user", 1.0)
    finally:
        database.close()


def test_entity_schema_keeps_unexpected_legacy_tables(archive_home, caplog):
    database = legacy_database(archive_home / "legacy-tables.db")
    try:
        database.execute("CREATE TABLE knowledge_facts (id TEXT)")
        database.execute("CREATE TABLE knowledge_edges (id TEXT)")
        database.execute("INSERT INTO knowledge_facts VALUES ('retain')")
        database.execute("INSERT INTO knowledge_edges VALUES ('edge')")
        database.commit()
        with caplog.at_level("WARNING", logger=module.__name__):
            module._migrate_v7(database)
        assert "knowledge_facts" in caplog.text and "knowledge_edges" in caplog.text
        assert (
            database.execute("SELECT id FROM knowledge_facts").fetchone()[0] == "retain"
        )
        assert (
            database.execute("SELECT id FROM knowledge_edges").fetchone()[0] == "edge"
        )
    finally:
        database.close()


def test_failed_version_marker_can_resume_after_real_database_repair(archive_home):
    path = archive_home / "interrupted.db"
    database = legacy_database(path)
    database.execute(
        "CREATE TRIGGER stop_v3 BEFORE INSERT ON schema_version WHEN NEW.version=3 BEGIN SELECT RAISE(ABORT, 'version marker denied'); END"
    )
    database.close()
    store = SemanticArchive(db_path=path, embedding_dim=3)
    try:
        with pytest.raises(sqlite3.Error, match="version marker denied"):
            store.init()
        assert [
            row[0]
            for row in store.db.execute(
                "SELECT version FROM schema_version ORDER BY version"
            )
        ] == [1, 2]
        assert "recall_count" in {
            row[1] for row in store.db.execute("PRAGMA table_info(semantic_memory)")
        }
        store.db.rollback()
        store.db.execute("DROP TRIGGER stop_v3")
    finally:
        store.close()
    store.init()
    try:
        assert (
            store.db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 10
        )
        assert store.get("pref.editor").value == "vim"
    finally:
        store.close()


def test_capabilities_follow_live_and_pinned_graph_configuration(archive):
    assert archive.capabilities().transactional_batch
    assert archive.capabilities().event_log and archive.capabilities().full_text_search
    assert not archive.capabilities().vector and not archive.graph_enabled
    config = AppConfig.load()
    config.memory.graph_enabled = True
    config.save()
    assert archive.graph_enabled and archive.capabilities().entity_graph
    archive.graph_enabled = 0
    assert not archive.graph_enabled
    archive.graph_enabled = None
    assert archive.graph_enabled
    assert archive._graph is None


def test_graph_and_alias_cache_share_connection_and_require_invalidation(archive):
    assert archive.graph.db is archive.db and archive.graph is archive.graph
    index = archive.alias_index
    entity = archive.graph.upsert_entity("Dana Quinn", "person", aliases=["@dquinn"])
    assert archive.alias_index is index and index.find("Dana Quinn") == []
    archive.invalidate_alias_index()
    archive.invalidate_alias_index()
    rebuilt = archive.alias_index
    assert rebuilt is not index and rebuilt is archive.alias_index
    assert rebuilt.find("@dquinn")[0].entity_id == entity
    assert archive._alias_index_gen == archive._alias_generation == 2


def test_graph_linking_and_boosts_obey_pin_and_real_failure_fallback(archive):
    entity = archive.graph.upsert_entity("Dana Quinn", "person")
    archive.invalidate_alias_index()
    archive.link_written_record(
        from_kind="semantic",
        from_ref="pref.note",
        text="Dana Quinn likes concise replies.",
    )
    assert archive.graph.backlinks(entity) == []
    archive.graph_enabled = True
    archive.link_written_record(
        from_kind="semantic",
        from_ref="pref.note",
        text="Dana Quinn likes concise replies.",
    )
    assert archive.graph.backlinks(entity)
    assert archive._graph_boosts("Dana Quinn") == archive.graph.recall_refs(
        "Dana Quinn", index=archive.alias_index
    )
    archive.db.execute("DROP TABLE mem_links")
    archive.db.commit()
    assert archive._graph_boosts("Dana Quinn") == {}
    archive.link_written_record(
        from_kind="semantic", from_ref="pref.another", text="Dana Quinn likes tea."
    )


def test_close_retains_existing_graph_cache_contract(archive):
    graph = archive.graph
    index = archive.alias_index
    archive.close()
    archive.init()
    assert archive.graph is graph and archive.alias_index is index
    with pytest.raises(sqlite3.Error):
        graph.entities()
    assert archive.db.execute("SELECT COUNT(*) FROM mem_entities").fetchone()[0] == 0


def test_typed_episode_dedup_does_not_reassign_existing_axes(archive):
    original = MemoryRecord(
        id="",
        kind=MemoryKind.EPISODIC,
        text="An amber deployment plan with a repeatable checklist.",
        scope=MemoryScope.SESSION,
        scope_ref="first",
        category="event",
    )
    archive.put([original])
    episode = archive.query(kinds={"episodic"})[0]
    assert episode.scope_ref == "first"
    repeated = MemoryRecord(
        id="",
        kind=MemoryKind.EPISODIC,
        text=original.text,
        scope=MemoryScope.WORKSPACE,
        scope_ref="second",
    )
    archive.put([repeated])
    records = archive.query(kinds={"episodic"})
    assert len(records) == 1 and records[0].id == episode.id
    assert records[0].scope == MemoryScope.SESSION and records[0].scope_ref == "first"


def test_typed_semantic_refusal_does_not_apply_requested_axes(archive):
    archive.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    archive.put(
        [
            MemoryRecord(
                id="pref.editor",
                kind=MemoryKind.SEMANTIC,
                value="helix",
                confidence=0.9,
                source="automated",
                scope=MemoryScope.SESSION,
                scope_ref="blocked",
            )
        ]
    )
    stored = archive.get("pref.editor")
    assert (
        stored.value == "vim"
        and stored.scope == MemoryScope.GLOBAL
        and stored.scope_ref is None
    )


def test_axis_projection_preserves_tier_and_nonzero_recall_when_omitted(archive):
    archive.put(
        [
            MemoryRecord(
                id="pref.note",
                kind=MemoryKind.SEMANTIC,
                value="memo",
                confidence=1.0,
                source="user_explicit",
                scope=MemoryScope.SESSION,
                tier=MemoryTier.WORKING,
                recall_count=8,
                visit_count=4,
            )
        ]
    )
    update = MemoryRecord(
        id="pref.note",
        kind=MemoryKind.SEMANTIC,
        scope=MemoryScope.WORKSPACE,
        scope_ref="/owned",
    )
    update.tier = None
    archive._apply_axes("semantic_memory", "key", "pref.note", update)
    stored = archive.get("pref.note")
    assert stored.tier == MemoryTier.WORKING and stored.recall_count == 8
    assert (
        stored.scope == MemoryScope.WORKSPACE
        and stored.scope_ref == "/owned"
        and stored.visit_count == 0
    )


def test_default_axes_skip_database_access_before_initialization(archive_home):
    store = SemanticArchive(db_path=archive_home / "unopened.db")
    record = MemoryRecord(id="pref.default", kind=MemoryKind.SEMANTIC)
    record.tier = None
    store._apply_axes("semantic_memory", "key", record.id, record)
    assert not store._db_path.exists()


def test_batch_retains_earlier_commits_when_later_value_cannot_serialize(archive):
    valid = MemoryRecord(
        id="pref.accepted",
        kind=MemoryKind.SEMANTIC,
        value="kept",
        confidence=1.0,
        source="user_explicit",
    )
    invalid = MemoryRecord(
        id="pref.invalid",
        kind=MemoryKind.SEMANTIC,
        value="valid at construction",
        confidence=1.0,
        source="user_explicit",
    )
    invalid.value = {"unsupported": {1, 2}}
    with pytest.raises(TypeError):
        archive.put([valid, invalid])
    assert archive.get("pref.accepted").value == "kept"
    independent = sqlite3.connect(str(archive._db_path))
    try:
        assert (
            independent.execute(
                "SELECT value_json FROM semantic_memory WHERE key='pref.accepted'"
            ).fetchone()[0]
            == '"kept"'
        )
    finally:
        independent.close()


def test_query_keeps_order_filters_negative_slices_and_deleted_records(archive):
    archive.put(
        [
            MemoryRecord(
                id=f"pref.item{index}",
                kind=MemoryKind.SEMANTIC,
                value=f"value{index}",
                confidence=1.0,
                source="user_explicit",
                scope=MemoryScope.WORKSPACE,
                scope_ref="/owned" if index < 2 else "/other",
            )
            for index in range(3)
        ]
    )
    assert [
        record.id
        for record in archive.query(scope="workspace", scope_ref="/owned", limit=-1)
    ] == ["pref.item0"]
    assert archive.delete("pref.item0", source="operator")
    assert [record.id for record in archive.query(scope_ref="/owned")] == ["pref.item1"]
    assert [
        record.id for record in archive.query(scope_ref="/owned", include_deleted=True)
    ] == ["pref.item0", "pref.item1"]


def test_delete_prefers_live_semantic_identity_before_episode(archive):
    archive.set_semantic("pref.shared", "fact", 1.0, "user_explicit")
    archive.write_episodic(
        "An episodic fragment with a colliding persisted identifier."
    )
    archive.db.execute("UPDATE episodic_memories SET id='pref.shared'")
    archive.db.commit()
    assert archive.delete("pref.shared")
    assert archive.query(kinds={"episodic"})
    assert archive.delete("pref.shared")
    assert archive.query(kinds={"episodic"}) == []


def test_vector_contract_accepts_real_explicit_vector_without_embedder(archive):
    assert archive.embed("unconfigured") is None
    assert archive.vector_query(text="amber") == []
    archive.write_episodic(
        "Amber release notes describe the integration boundary.",
        embedding=[1.0, 0.0, 0.0],
    )
    hits = archive.vector_query(
        embedding=[1.0, 0.0, 0.0], text="amber", k=1, kinds={"semantic"}
    )
    assert hits and "Amber" in hits[0]["text"]
    assert not archive.capabilities().vector


def test_event_append_returns_actual_persisted_identifier_and_page(archive):
    identity = archive.append_event(
        event_type="create",
        memory_type="semantic",
        memory_key="pref.note",
        old_value=None,
        new_value="value",
        source="operator",
    )
    row = archive.db.execute(
        "SELECT * FROM memory_events WHERE id=?", (identity,)
    ).fetchone()
    assert row["source"] == "operator" and row["memory_key"] == "pref.note"
    assert archive.read_events(limit=1) == [dict(row)]
    assert archive.read_events(offset=1) == []


def test_corrupt_optional_faiss_files_do_not_block_database_start(archive_home):
    path = archive_home / "corrupt-index" / "memory.db"
    path.parent.mkdir()
    (path.parent / "memory.faiss").write_bytes(b"invalid archive index")
    (path.parent / "memory.ids.json").write_text("not-json")
    store = SemanticArchive(db_path=path, embedding_dim=3)
    store.init()
    try:
        assert (
            store.db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 10
        )
        assert store.set_semantic("pref.editor", "vim", 1.0, "user_explicit") is None
    finally:
        store.close()
