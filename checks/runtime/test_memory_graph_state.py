import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from gideon.cognition.memory_graph import (
    AliasIndex,
    Entity,
    MemoryGraph,
    Mention,
    _iso_days_ago,
    _merge_aliases,
    _now,
    _tokenize,
    new_entity_id,
    sqlite3,
)
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig


@pytest.fixture
def archive(tmp_path, monkeypatch):
    home = tmp_path / "graph-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    config = AppConfig.load()
    config.memory.graph_enabled = False
    config.save()
    store = SemanticArchive(db_path=home / "memory.db", embedding_dim=3)
    store.init()
    yield store
    store.close()


def _edge(
    graph,
    reference,
    entity,
    *,
    kind="semantic",
    relation="mentions",
    confidence=1.0,
    provenance="extracted",
):
    return graph.add_link(
        from_kind=kind,
        from_ref=reference,
        to_entity=entity,
        link_type=relation,
        confidence=confidence,
        provenance=provenance,
    )


def _volunteer(
    graph,
    reference,
    *,
    name="Orbit",
    arm="exact",
    kind="semantic",
    count=0,
    confidence=0.8,
):
    return graph.log_volunteer(
        entity_id="orbit",
        entity_name=name,
        arm=arm,
        confidence=confidence,
        from_kind=kind,
        record_ref=reference,
        recall_at_volunteer=count,
        session_key="local-session",
    )


def test_phrase_index_preserves_registration_counts_longest_matches_and_id_order():
    index = AliasIndex()
    for identity in ("second", "first", "second"):
        assert index.add(identity, "Dana Quinn")
    assert not index.add("short", "AI")
    assert (
        index.add_entity(Entity("person", "Dana", "person", aliases=("AI", "AI Lab")))
        == 2
    )
    text = "DANA--Quinn met Dana and AI Lab"
    mentions = index.find(text)
    assert [(item.entity_id, item.matched) for item in mentions] == [
        ("second", "DANA--Quinn"),
        ("first", "DANA--Quinn"),
        ("person", "Dana"),
        ("person", "AI Lab"),
    ]
    assert all(text[item.start : item.end] == item.matched for item in mentions)
    assert index.find("Announcement and AILab") == []


def test_phrase_index_unknown_candidates_preserve_occurrences_and_exact_span_exclusion():
    index = AliasIndex()
    index.add("dana", "Dana Quinn")
    assert index.unknown_capitalized("Dana Quinn met Nora Price; Nora Price left.") == [
        "Nora Price",
        "Nora Price",
    ]
    assert index.unknown_capitalized("Dana Quinn Reports") == ["Dana Quinn Reports"]
    assert AliasIndex().find(None) == []
    assert _tokenize("Dana's @Orbit") == [("dana's", 0, 6), ("orbit", 8, 13)]
    text = "x" * 120 + " Atlas " + "y" * 120
    mention = Mention("atlas", "Atlas", 121, 126)
    assert mention.context(text) == text[21:226]


@pytest.mark.parametrize(
    "existing,incoming,expected",
    [
        ('["old"]', ["new", "new", "  ", ""], ["new", "old"]),
        ('{"key": true}', [" raw "], [" raw ", "key"]),
        ("not-json", ["new"], ["new"]),
        ('[["unhashable"]]', ["new"], ["new"]),
    ],
)
def test_alias_merge_keeps_legacy_decoding_and_whitespace(existing, incoming, expected):
    assert _merge_aliases(existing, incoming) == expected


def test_entity_upsert_preserves_authority_and_new_alias_admission(archive):
    graph = archive.graph
    identity = graph.upsert_entity(
        "  Atlas  ",
        "project",
        aliases=[" ", "", "Orbit", "Orbit"],
        source="user",
        entity_id="atlas",
    )
    assert identity == "atlas"
    assert graph.entities()[0].aliases == ("", " ", "Orbit")
    assert (
        graph.upsert_entity(
            "ATLAS",
            "topic",
            aliases=["Compass", " "],
            source="knowledge:k",
            entity_id="different",
        )
        == "atlas"
    )
    entity = graph.entities()[0]
    assert (
        entity.name == "Atlas"
        and entity.entity_type == "project"
        and entity.source == "user"
    )
    assert entity.aliases == ("", " ", "Compass", "Orbit")
    with pytest.raises(ValueError, match="unknown entity_type"):
        graph.upsert_entity("", "invalid")
    assert graph.delete_entity("atlas")
    assert (
        graph.entities() == [] and graph.entities(include_deleted=True)[0].id == "atlas"
    )
    replacement = graph.upsert_entity("Atlas", "tool")
    assert replacement != "atlas" and graph.entities()[0].id == replacement
    assert not graph.delete_entity("atlas")


def test_entity_alias_rows_degrade_malformed_values(archive):
    graph = archive.graph
    graph.upsert_entity("Atlas", "project", entity_id="atlas")
    archive.db.execute("UPDATE mem_entities SET aliases='broken-json' WHERE id='atlas'")
    archive.db.commit()
    assert graph.entities()[0].aliases == ()
    assert graph.build_index().find("Atlas")[0].entity_id == "atlas"


def test_links_share_driver_record_exact_wal_identity_and_preserve_original_metadata(
    archive,
):
    graph = archive.graph
    graph.upsert_entity("Atlas", "project", entity_id="atlas")
    assert _edge(graph, "pref.item", "atlas", confidence=0.4, provenance="user")
    assert not _edge(graph, "pref.item", "atlas", confidence=0.9, provenance="changed")
    row = graph.links_from("semantic", "pref.item")[0]
    assert row["confidence"] == 0.4 and row["provenance"] == "user"
    assert graph.stats("atlas")["inbound_count"] == 1
    event = archive.db.execute(
        "SELECT * FROM memory_events WHERE event_type='link_add'"
    ).fetchone()
    assert json.loads(event["new_value"]) == dict(
        from_kind="semantic",
        from_ref="pref.item",
        to_entity="atlas",
        to_ref=None,
        link_type="mentions",
    )
    assert graph.remove_link(row["id"])
    removal = archive.db.execute(
        "SELECT * FROM memory_events WHERE event_type='link_remove'"
    ).fetchone()
    assert removal["old_value"] == event["new_value"] and removal["new_value"] is None
    assert graph.stats("atlas")["inbound_count"] == 0
    assert not graph.remove_link(row["id"])
    assert graph.stats("absent") == {"entity_id": "absent", "inbound_count": 0}


def test_record_link_clear_coalesces_stats_decrements_and_keeps_other_records(archive):
    graph = archive.graph
    graph.upsert_entity("Atlas", "project", entity_id="atlas")
    for relation in ("mentions", "about", "references"):
        _edge(graph, "shared", "atlas", relation=relation)
    _edge(graph, "other", "atlas")
    graph.add_link(
        from_kind="semantic", from_ref="shared", to_ref="other", link_type="references"
    )
    assert graph.drop_links_for("semantic", "shared") == 4
    assert graph.stats("atlas")["inbound_count"] == 1
    assert graph.drop_links_for("semantic", "shared") == 0
    assert graph.backlinks("atlas")[0]["from_ref"] == "other"
    assert graph.backlinks("atlas", limit=0) == []


def test_any_integrity_rejection_retains_duplicate_reinforcement_behavior(archive):
    graph = archive.graph
    graph.upsert_entity("Atlas", "project", entity_id="atlas")
    archive.db.execute(
        "CREATE TRIGGER reject_edge BEFORE INSERT ON mem_links BEGIN SELECT RAISE(ABORT, 'edge rejected'); END"
    )
    archive.db.commit()
    assert not _edge(graph, "not-inserted", "atlas")
    assert graph.links_from("semantic", "not-inserted") == []
    assert graph.stats("atlas")["inbound_count"] == 1


def test_wal_rejection_does_not_roll_back_committed_graph_edge(archive):
    graph = archive.graph
    graph.upsert_entity("Atlas", "project", entity_id="atlas")
    archive.db.execute(
        "CREATE TRIGGER reject_graph_wal BEFORE INSERT ON memory_events WHEN NEW.event_type='link_add' BEGIN SELECT RAISE(ABORT, 'wal rejected'); END"
    )
    archive.db.commit()
    assert _edge(graph, "survives", "atlas")
    assert graph.links_from("semantic", "survives")
    assert (
        archive.db.execute(
            "SELECT COUNT(*) FROM memory_events WHERE event_type='link_add'"
        ).fetchone()[0]
        == 0
    )


def test_query_order_evidence_and_recall_weights_preserve_link_multiplicity(archive):
    graph = archive.graph
    for name, identity in (("Atlas", "atlas"), ("Nora Quinn", "nora")):
        graph.upsert_entity(name, "project", entity_id=identity)
    _edge(graph, "record", "atlas")
    _edge(graph, "record", "atlas", relation="about")
    _edge(graph, "record", "nora")
    assert graph.resolve_query("Nora Quinn Atlas Nora Quinn") == ["nora", "atlas"]
    assert graph.recall_evidence("Nora Quinn Atlas") == {
        "record": ["Nora Quinn", "Atlas"]
    }
    assert graph.recall_refs("Nora Quinn Atlas")["record"] == pytest.approx(0.75)
    assert graph.recall_refs("Atlas", limit=1)["record"] == pytest.approx(0.25)
    assert graph.recall_refs("unrelated") == {}


def test_proposal_reference_cap_retains_existing_count_plateau(archive):
    graph = archive.graph
    for number in range(52):
        count = graph.tally_proposal("Nora Quinn", f"record-{number}")
    assert count == 51
    row = archive.db.execute("SELECT * FROM mem_entity_proposals").fetchone()
    assert len(json.loads(row["refs"])) == 50 and row["mention_count"] == 51
    assert graph.tally_proposal("Nora Quinn", "record-51") == 51
    assert graph.tally_proposal("Nora Quinn", "record-0") == 51
    assert graph.proposals()[0]["name"] == "Nora Quinn"


def test_proposal_pruning_thresholds_and_accept_reject_use_real_rows(archive):
    graph = archive.graph
    for reference in ("a", "b", "c"):
        graph.tally_proposal("Alias Name", reference)
    graph.tally_proposal("Below Threshold", "a")
    graph.upsert_entity("Known", "person", aliases=["Alias Name", "Below Threshold"])
    assert graph.proposals() == []
    assert (
        archive.db.execute("SELECT name FROM mem_entity_proposals").fetchone()[0]
        == "Below Threshold"
    )
    assert graph.proposals(threshold=1) == []
    graph.tally_proposal("Accepted Name", "x")
    identity = graph.accept_proposal("Accepted Name", "person", source="user")
    assert any(entity.id == identity for entity in graph.entities())
    assert not graph.reject_proposal("Accepted Name")
    graph.tally_proposal("Rejected Name", "x")
    assert graph.reject_proposal("Rejected Name")


def test_projection_counts_leg_pairs_and_combines_metadata(archive):
    graph = archive.graph
    for name, identity in (("Alpha", "a"), ("Beta", "b"), ("Alone", "z")):
        graph.upsert_entity(name, "project", entity_id=identity)
    _edge(graph, "record", "a", confidence=0.4, provenance="human")
    _edge(graph, "record", "a", relation="about", confidence=0.9, provenance="derived")
    _edge(graph, "record", "b", confidence=0.8, provenance="human")
    _edge(
        graph, "record", "b", relation="references", confidence=0.5, provenance="import"
    )
    _edge(graph, "record", "missing")
    graph_view = graph.entity_graph()
    assert [node["name"] for node in graph_view["nodes"]] == ["Alone", "Alpha", "Beta"]
    assert graph_view["edges"] == [
        {
            "from": "a",
            "to": "b",
            "records": 4,
            "link_types": ["about", "mentions", "references"],
            "provenances": ["derived", "human", "import"],
            "confidence": 0.8,
        }
    ]


def test_orphan_counts_include_any_outbound_link_and_summary_collapses_equal_refs(
    archive,
):
    graph = archive.graph
    archive.set_semantic("pref.shared", "some value", 0.9, "seed")
    episode = archive.write_episodic("separate event")
    graph.upsert_entity("Unused", "topic", entity_id="unused")
    graph.add_link(
        from_kind="semantic",
        from_ref="pref.shared",
        to_ref="batch",
        link_type="temporal_proximity",
    )
    graph.add_link(
        from_kind="episodic",
        from_ref="pref.shared",
        to_ref="batch",
        link_type="temporal_proximity",
    )
    counts = graph.summary()
    assert (
        episode and counts["semantic_orphans"] == 0 and counts["episodic_orphans"] == 1
    )
    assert (
        counts["phantom_entities"] == 1
        and counts["links"] == 2
        and counts["linked_records"] == 1
    )


def test_volunteer_metrics_use_count_deltas_exclude_episodic_and_deduplicate_qrels(
    archive,
):
    graph = archive.graph
    for key in ("pref.used", "pref.unused"):
        archive.set_semantic(key, key, 0.9, "seed")
    archive.record_recall(["pref.used"])
    first = _volunteer(graph, "pref.used", count=1)
    assert first > 0
    assert graph.volunteer_precision()["overall"] == dict(n=1, used=0, precision=0.0)
    _volunteer(graph, "pref.used", name=" Orbit ", arm="alias", count=1)
    _volunteer(graph, "pref.unused", arm="alias")
    _volunteer(graph, "pref.used", name="Ignored", kind="episodic")
    _volunteer(graph, "missing", name="Missing")
    archive.record_recall(["pref.used"])
    assert graph.volunteer_qrels() == {"Orbit": ["pref.used"]}
    assert graph.volunteer_precision() == {
        "arms": {
            "alias": dict(n=2, used=1, precision=0.5),
            "exact": dict(n=2, used=1, precision=0.5),
        },
        "overall": dict(n=4, used=2, precision=0.5),
    }
    archive.delete_semantic("pref.used", "seed")
    assert graph.volunteer_qrels() == {"Orbit": ["pref.used"]}
    columns = {
        row[1] for row in archive.db.execute("PRAGMA table_info(mem_volunteer_events)")
    }
    assert "text" not in columns and "content" not in columns


def test_volunteer_window_prune_and_unavailable_table_fallbacks(archive):
    graph = archive.graph
    archive.set_semantic("pref.used", "value", 0.9, "seed")
    _volunteer(graph, "pref.used")
    archive.record_recall(["pref.used"])
    archive.db.execute(
        "UPDATE mem_volunteer_events SET created_at='2000-01-01T00:00:00+00:00'"
    )
    archive.db.commit()
    assert graph.volunteer_qrels(window_days=7) == {}
    assert graph.volunteer_precision(window_days=7)["overall"]["n"] == 0
    assert graph.volunteer_qrels(window_days=0) == {"Orbit": ["pref.used"]}
    assert graph.prune_volunteer_events(keep_days=90) == 1
    assert graph.prune_volunteer_events() == 0
    archive.db.execute("DROP TABLE mem_volunteer_events")
    archive.db.commit()
    assert _volunteer(graph, "pref.used") == 0
    assert graph.volunteer_qrels() == {}
    assert graph.volunteer_precision() == {
        "arms": {},
        "overall": dict(n=0, used=0, precision=0.0),
    }
    assert graph.prune_volunteer_events() == 0


def test_volunteer_input_errors_remain_distinct_from_sqlite_degradation(archive):
    graph = archive.graph
    with pytest.raises(ValueError):
        _volunteer(graph, "pref.any", confidence="invalid")
    with pytest.raises(ValueError):
        graph.volunteer_qrels(window_days="invalid")
    with pytest.raises(TypeError):
        graph.prune_volunteer_events(keep_days="invalid")
    assert _volunteer(graph, "pref.any", confidence=math.nan) == 0


def test_graph_helpers_produce_bounded_ids_and_utc_timestamps():
    identity = new_entity_id()
    assert identity.startswith("e-") and len(identity) == 10
    before = datetime.now(timezone.utc)
    recent = datetime.fromisoformat(_now())
    cutoff = datetime.fromisoformat(_iso_days_ago(2))
    clamped = datetime.fromisoformat(_iso_days_ago(-2))
    after = datetime.now(timezone.utc)
    assert before <= recent <= after
    assert before - timedelta(days=2) <= cutoff <= after - timedelta(days=2)
    assert before <= clamped <= after
