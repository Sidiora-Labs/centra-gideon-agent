import json
import math
import random
import re
import sqlite3

import pytest

from gideon.cognition import memory_graph_export as export
from gideon.cognition import memory_linker as linker
from gideon.cognition import memory_topology as topology
from gideon.cognition.knowledge.reports import SpecError
from gideon.cognition.memory_graph import Mention
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig


@pytest.fixture
def archive(tmp_path, monkeypatch):
    home = tmp_path / "topology-home"
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


def _entity(graph, name, identity):
    return graph.upsert_entity(name, "project", entity_id=identity)


def _link(graph, reference, entity, kind="semantic", relation="mentions"):
    return graph.add_link(
        from_kind=kind, from_ref=reference, to_entity=entity, link_type=relation
    )


def _island(document):
    match = re.search(r"<!-- gideon-memory-graph\n(.*?)\n-->", document, re.S)
    assert match
    return json.loads(match.group(1))


def test_cooccurrence_counts_records_not_link_types_and_keeps_kinds_separate(archive):
    graph = archive.graph
    for name, identity in (("Alpha", "a"), ("Beta", "b"), ("Gamma", "c")):
        _entity(graph, name, identity)
    for entity in ("a", "b", "c"):
        _link(graph, "first", entity)
    _link(graph, "first", "a", relation="about")
    _link(graph, "first", "a", kind="episodic")
    _link(graph, "first", "b", kind="episodic")
    graph.add_link(
        from_kind="semantic",
        from_ref="first",
        to_ref="reference",
        link_type="references",
    )
    assert topology.cooccurrence_edges(archive.db) == {
        ("a", "b"): 2.0,
        ("a", "c"): 1.0,
        ("b", "c"): 1.0,
    }
    with sqlite3.connect(archive._db_path) as tuples:
        assert topology.cooccurrence_edges(tuples) == topology.cooccurrence_edges(
            archive.db
        )


@pytest.mark.parametrize(
    "nodes,edges,expected",
    [
        ([], {}, {}),
        (["b", "a"], {}, {"a": 0, "b": 1}),
        (["b", "a"], {("a", "b"): 1.0}, {"a": 0, "b": 0}),
        (
            ["b", "a"],
            {("a", "a"): 50.0, ("b", "b"): 50.0, ("a", "b"): 1.0},
            {"a": 0, "b": 1},
        ),
        (["b", "a"], {("a", "missing"): 50.0}, {"a": 0, "b": 1}),
        (["b", "a"], {("a", "b"): -1.0}, {"a": 0, "b": 1}),
    ],
)
def test_local_moves_preserve_weighted_self_loops_and_zero_mass(nodes, edges, expected):
    assert topology._modularity_partition(nodes, edges, random.Random(42)) == expected


def test_no_mass_does_not_consume_rng_and_nan_mass_retains_visit_order_consumption():
    rng = random.Random(42)
    before = rng.getstate()
    topology._modularity_partition(["a", "b"], {}, rng)
    assert rng.getstate() == before
    topology._modularity_partition(["a", "b"], {("a", "b"): math.nan}, rng)
    assert rng.getstate() != before


def test_multilevel_assignment_and_persistence_include_isolated_entities(archive):
    graph = archive.graph
    for identity, name in (
        ("a", "Alpha"),
        ("b", "Beta"),
        ("c", "Gamma"),
        ("d", "Delta"),
        ("e", "Epsilon"),
        ("z", "Alone"),
    ):
        _entity(graph, name, identity)
    for number, pair in enumerate((("a", "b"), ("a", "c"), ("b", "c"), ("d", "e"))):
        for entity in pair:
            _link(graph, f"record-{number}", entity)
    expected = {"a": 0, "b": 0, "c": 0, "d": 1, "e": 1, "z": 2}
    assert topology.detect_communities(archive.db) == expected
    before = {
        row["entity_id"]: row["inbound_count"]
        for row in archive.db.execute("SELECT * FROM mem_link_stats")
    }
    assert topology.write_communities(archive) == 6
    stored = {
        row["entity_id"]: (row["community"], row["inbound_count"])
        for row in archive.db.execute("SELECT * FROM mem_link_stats")
    }
    assert {key: value[0] for key, value in stored.items()} == expected
    assert all(stored[key][1] == count for key, count in before.items())
    assert stored["z"] == (2, 0)
    members = topology.community_members(archive.db)
    assert members[0] == [("a", "Alpha", 2), ("b", "Beta", 2), ("c", "Gamma", 2)]
    assert topology.write_communities(archive) == 6
    assert topology.community_members(archive.db) == members
    with sqlite3.connect(archive._db_path) as tuples:
        assert topology.detect_communities(tuples) == expected


def test_topology_caption_reads_stored_groups_and_stops_at_first_unfittable_line(
    archive,
):
    graph = archive.graph
    for identity, name in (("a", "A" * 300), ("b", "Beta"), ("c", "Gamma")):
        _entity(graph, name, identity)
        archive.db.execute(
            "INSERT INTO mem_link_stats(entity_id, inbound_count, community) VALUES (?, ?, ?)",
            (identity, 9 if identity == "a" else 1, 0 if identity != "c" else 1),
        )
    archive.db.commit()
    assert topology.topology_block(archive.db, max_chars=200) == ""
    assert topology.topology_block(archive.db, max_chars=0) == ""
    full = topology.topology_block(archive.db, max_chars=500)
    assert "0: " + "A" * 300 + ", Beta (2)" in full
    assert "1: Gamma (1)" in full and len(full) <= 500
    assert topology.topology_block(archive.db, max_chars=len(full)) == full
    assert "1: Gamma" not in topology.topology_block(
        archive.db, max_chars=len(full) - 1
    )


def test_dangling_link_on_a_merged_level_retains_existing_key_error(archive):
    graph = archive.graph
    _entity(graph, "Alpha", "a")
    _entity(graph, "Beta", "b")
    for entity in ("a", "b", "missing"):
        _link(graph, "joint", entity)
    with pytest.raises(KeyError, match="missing"):
        topology.detect_communities(archive.db)


@pytest.mark.parametrize(
    "key,kind,expected",
    [
        ("USER.PERSONA.any", "project", "about"),
        ("PREF.FACET.IDENTITY.any", "person", "about"),
        ("project.other.note", "project", "same_project"),
        ("project.other.note", "person", "references"),
    ],
)
def test_link_cascade_retains_first_structural_match(key, kind, expected):
    mention = Mention("known", "Atlas", 0, 5)
    assert (
        linker.classify_link(
            key, "Atlas https://example.test/report", mention, {"known": kind}
        )
        == expected
    )


def test_repeated_mentions_and_replace_false_report_only_new_links(archive):
    graph = archive.graph
    identity = _entity(graph, "Atlas", "atlas")
    index = graph.build_index()
    first = linker.link_record(
        graph, index, from_kind="semantic", from_ref="pref.note", text="Atlas and Atlas"
    )
    assert first == {"mentions": 2, "links": 1, "proposals": 0, "entities": [identity]}
    second = linker.link_record(
        graph,
        index,
        from_kind="semantic",
        from_ref="pref.note",
        text="Atlas and Atlas",
        replace=False,
    )
    assert second == {"mentions": 2, "links": 0, "proposals": 0, "entities": []}
    assert graph.stats(identity)["inbound_count"] == 1


def test_temporal_batch_edges_are_outgoing_and_skip_equal_refs_across_kinds(archive):
    graph = archive.graph
    index = graph.build_index()
    for kind, reference in (
        ("semantic", "same"),
        ("episodic", "same"),
        ("episodic", "later"),
    ):
        linker.link_record(
            graph, index, from_kind=kind, from_ref=reference, text="", batch_ref="batch"
        )
    assert {row["to_ref"] for row in graph.links_from("semantic", "same")} == {"batch"}
    assert {row["to_ref"] for row in graph.links_from("episodic", "same")} == {"batch"}
    assert {row["to_ref"] for row in graph.links_from("episodic", "later")} == {
        "batch",
        "same",
    }


def test_linking_failure_preserves_completed_edges_and_partial_report(archive):
    graph = archive.graph
    _entity(graph, "Atlas", "atlas")
    index = graph.build_index()
    archive.db.execute("DROP TABLE mem_entity_proposals")
    archive.db.commit()
    report = linker.link_record(
        graph,
        index,
        from_kind="semantic",
        from_ref="pref.partial",
        text="Atlas meets Nora Quinn",
        batch_ref="batch",
    )
    assert report == {"mentions": 1, "links": 1, "proposals": 0, "entities": ["atlas"]}
    assert [
        row["link_type"] for row in graph.links_from("semantic", "pref.partial")
    ] == ["mentions"]


def test_fact_seeding_keeps_value_casing_and_explicit_identity_cues(archive):
    rows = [
        ("project.orbit.work", {"description": "ORBIT operations"}),
        ("project.name", "Zephyr"),
        ("pref.facet.identity.name", {"text": "Call me Nora Quinn"}),
        ("pref.facet.identity.style", {"text": "prefers short answers"}),
    ]
    for key, value in rows:
        assert archive.set_semantic(key, value, 0.9, "seed") is None
    assert linker.seed_from_memory_facts(archive.graph) == 3
    assert {
        (entity.name, entity.entity_type) for entity in archive.graph.entities()
    } == {("ORBIT", "project"), ("Zephyr", "project"), ("Nora Quinn", "person")}
    assert linker._text_of({"text": "", "description": "later"}) == ""
    assert linker._cased_in_text("orb", "ORBIT") is None


def test_knowledge_adoption_is_read_only_and_preserves_existing_entity_authority(
    archive, tmp_path
):
    path = tmp_path / "knowledge.db"
    with sqlite3.connect(path) as database:
        database.execute(
            "CREATE TABLE entities(id TEXT, name TEXT, entity_type TEXT, aliases TEXT)"
        )
        database.executemany(
            "INSERT INTO entities VALUES (?, ?, ?, ?)",
            [
                ("k1", "Atlas", "concept", '["Orbit"]'),
                ("k2", "Nora Quinn", "PERSON", "not-json"),
                ("k3", " ", "topic", "[]"),
            ],
        )
    before = path.read_bytes()
    archive.graph.upsert_entity("Atlas", "project", source="user")
    assert linker.seed_from_knowledge(archive.graph, path) == 2
    assert path.read_bytes() == before
    entities = {entity.name: entity for entity in archive.graph.entities()}
    assert (
        entities["Atlas"].entity_type == "project"
        and entities["Atlas"].source == "user"
    )
    assert entities["Atlas"].aliases == ("Orbit",)
    assert (
        entities["Nora Quinn"].source == "knowledge:k2"
        and entities["Nora Quinn"].aliases == ()
    )
    assert linker.seed_all(
        archive.graph, knowledge_db_path=tmp_path / "missing.db"
    ) == {"from_facts": 0, "from_knowledge": 0}


def test_invalid_knowledge_schema_degrades_without_creating_source_files(
    archive, tmp_path
):
    path = tmp_path / "absent.db"
    assert linker.seed_from_knowledge(archive.graph, path) == 0 and not path.exists()
    path.write_bytes(b"not a sqlite database")
    assert linker.seed_from_knowledge(archive.graph, path) == 0


def test_backfill_limits_each_table_and_replaces_temporal_links(archive):
    graph = archive.graph
    for number in range(3):
        archive.set_semantic(f"pref.note{number}", {"text": "Atlas"}, 0.9, "seed")
        archive.write_episodic(f"Atlas note {number}", conversation_id="session")
    _entity(graph, "Atlas", "atlas")
    before_ids = [
        row[0]
        for row in archive.db.execute("SELECT id FROM episodic_memories ORDER BY id")
    ]
    for identity in before_ids:
        graph.add_link(
            from_kind="episodic",
            from_ref=identity,
            to_ref="batch",
            link_type="temporal_proximity",
        )
    report = linker.backfill(graph, batch_size=1, limit=1)
    assert report["records_processed"] == 2 and report["links_created"] == 2
    assert [
        link["link_type"] for link in graph.links_from("episodic", before_ids[0])
    ] == ["mentions"]
    assert any(
        link["link_type"] == "temporal_proximity"
        for link in graph.links_from("episodic", before_ids[1])
    )
    assert linker.backfill(graph, batch_size=-1)["records_processed"] == 0
    with pytest.raises(ValueError, match="range"):
        linker.backfill(graph, batch_size=0)


def test_ring_geometry_and_crop_sizes_use_real_numeric_coordinates():
    assert export._positions(0) == [] and export._positions(-1) == []
    assert export._positions(1) == [(500.0, 500.0)]
    points = export._positions(10)
    assert points[1] == pytest.approx((500, 370))
    assert points[3] == pytest.approx((630, 500))
    assert points[9] == pytest.approx((500, 250))
    assert 'viewBox="0 0 1000 1000"' in export.render_graph_html({})
    assert 'viewBox="380.0 380.0 240.0 240.0"' in export.render_graph_html(
        {"nodes": [{"id": "one"}]}
    )
    assert export._fill(None) == export._fill("not-a-group") == "hsl(0 0% 62%)"
    assert export._fill(2.9) == export._fill(2)
    with pytest.raises(OverflowError):
        export._fill(math.inf)


def test_export_selects_bounded_graph_without_mutating_input():
    nodes = [
        dict(id=f"n{index}", name=f"Entity {index}", inbound_count=index)
        for index in range(601)
    ]
    edges = [
        dict(**{"from": "n1", "to": "n2"}, records=index) for index in range(1, 3002)
    ]
    edges.append({"from": "n0", "to": "n1", "records": 9999})
    graph = dict(nodes=nodes, edges=edges)
    document = export.render_graph_html(graph)
    selected = _island(document)
    assert len(selected["nodes"]) == 600 and len(selected["edges"]) == 3000
    assert selected["nodes"][0]["id"] == "n600" and selected["nodes"][-1]["id"] == "n1"
    assert (
        selected["edges"][0]["records"] == 3001
        and selected["edges"][-1]["records"] == 2
    )
    assert "showing the 600 most-linked of 601 entities" in document
    assert "showing the 3000 strongest of 3001 links" in document
    assert len(nodes) == 601 and nodes[0]["id"] == "n0" and len(edges) == 3002
    assert document.count("<circle ") == 600 and document.count("<line ") == 3000


def test_export_escapes_visible_labels_and_keeps_json_comment_parseable():
    name = 'A & B--C "quoted" <plain>'
    graph = {
        "nodes": [{"id": "x", "name": name, "entity_type": "project", "community": 0}],
        "edges": [],
    }
    document = export.render_graph_html(graph, generated_at="today & tomorrow")
    assert "A &amp; B--C &quot;quoted&quot; &lt;plain&gt;" in document
    assert "exported today &amp; tomorrow" in document
    assert _island(document) == graph
    with pytest.raises(SpecError, match="script element"):
        export.render_graph_html(
            {"nodes": [{"id": "bad", "name": "<script>alert(1)</script>"}]}
        )
