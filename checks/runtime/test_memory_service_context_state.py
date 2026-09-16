import json

import pytest

from gideon.cognition import memory_service
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_graph import sqlite3
from gideon.cognition.memory_push import Candidate
from gideon.cognition.memory_record import MemoryKind, MemoryRecord, MemoryScope
from gideon.cognition.memory_service import (
    MemoryService,
    _is_episodic,
    _push_text,
    normalize_workspace_ref,
    resolve_lesson_scope,
    service_for,
)
from gideon.cognition.memory_slots import BLOCK_ORDER, SlotCapExceeded
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.integrations.memory_providers.base import MemoryProvider


@pytest.fixture
def local_memory(tmp_path, monkeypatch):
    home = tmp_path / "service-home"
    home.mkdir()
    monkeypatch.setenv("GIDEON_HOME", str(home))
    (home / "active_models.json").write_text("{}")
    config = AppConfig.load()
    config.memory.graph_enabled = False
    config.save()
    journal = MemoryJournal(workspace=home / "workspace")
    journal.init()
    archive = SemanticArchive(db_path=home / "records.db", embedding_dim=3)
    archive.init()
    yield journal, archive
    archive.close()
    memory_service._services.pop(id(journal), None)


def test_workspace_scope_uses_canonical_directory_without_widening(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(checkout, target_is_directory=True)
    assert normalize_workspace_ref(f" {alias} ") == str(checkout)
    assert normalize_workspace_ref("checkout") == ""
    assert normalize_workspace_ref(None) == ""
    assert resolve_lesson_scope(" WORKSPACE ", str(alias)) == (
        MemoryScope.WORKSPACE,
        str(checkout),
    )
    assert resolve_lesson_scope(" ", str(alias)) == (MemoryScope.GLOBAL, None)


@pytest.mark.parametrize(
    "scope,workspace,message",
    [
        ("workspace", None, "workspace is required when scope='workspace'"),
        (
            "workspace",
            "relative",
            "workspace must be an absolute working-directory path (got 'relative'); use the working directory from your session context",
        ),
        (
            "SESSION",
            None,
            "scope 'session' is not writable for a lesson: use 'global' or 'workspace'",
        ),
        (
            "agent",
            None,
            "scope 'agent' is not writable for a lesson: use 'global' or 'workspace'",
        ),
        (
            "anywhere",
            None,
            "unknown scope 'anywhere': expected one of 'global', 'workspace'",
        ),
    ],
)
def test_scope_refusals_remain_exact(scope, workspace, message):
    with pytest.raises(ValueError) as caught:
        resolve_lesson_scope(scope, workspace)
    assert str(caught.value) == message


@pytest.mark.parametrize(
    "value,expected",
    [
        ('"  reminder  "', "reminder"),
        ('{"text":" ","rule":" first ","value":"second"}', "first"),
        ({"description": " detail "}, "detail"),
        ({"text": 3, "value": " content "}, "content"),
        ("not json", ""),
        ("[]", ""),
        (None, ""),
    ],
)
def test_volunteer_claim_decoding(value, expected):
    assert _push_text({"value_json": value}) == expected


def test_record_kind_checks_use_real_typed_records():
    assert _is_episodic(MemoryRecord(id="event", kind=MemoryKind.EPISODIC))
    assert not _is_episodic(MemoryRecord(id="fact", kind=MemoryKind.SEMANTIC))


def test_null_provider_is_disabled_and_returns_independent_collections():
    service = MemoryService.over_vector_store(None)
    provider = service.provider
    assert isinstance(provider, MemoryProvider)
    assert provider.name == "null" and provider.init() is None
    assert not any(provider.capabilities().to_dict().values())
    assert (
        provider.put(
            [MemoryRecord(id="unused", kind=MemoryKind.SEMANTIC, value="data")]
        )
        is None
    )
    assert provider.get("unused") is None and not provider.delete("unused")
    records = provider.query()
    records.append("caller-owned")
    assert provider.query() == provider.vector_query() == provider.read_events() == []
    assert provider.render_markdown_context() == []
    assert provider.embed("unused") is None and provider.append_event() == 0
    assert service.get_context(l1_manifest=True) == ""
    assert service.capabilities().full_text_search is True
    assert service.apply_vault_edit("pref.editor", "vim") == (False, "no vector store")


def test_attachment_is_live_and_explicit_archive_has_precedence(local_memory, tmp_path):
    journal, archive = local_memory
    discovered = MemoryService(journal)
    assert not discovered.has_vector
    journal.vector_store = archive
    assert discovered.has_vector and not discovered.can_vector_search
    assert discovered.capabilities() == archive.capabilities()
    other = SemanticArchive(db_path=tmp_path / "override.db", embedding_dim=3)
    other.init()
    try:
        archive.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
        other.set_semantic("pref.editor", "helix", 1.0, "user_explicit")
        override = MemoryService(journal, vector_store=other)
        assert "helix" in override.semantic_context()
        assert "vim" in discovered.semantic_context()
        journal.vector_store = None
        assert not discovered.has_vector and override.has_vector
    finally:
        other.close()


def test_filesystem_fallback_and_cache_identity_use_real_journal(local_memory):
    journal, archive = local_memory
    journal.add_preference("Prefer amber terminology in responses.")
    service = service_for(journal)
    assert service is service_for(journal)
    hits = service.fts_fallback_search("amber")
    assert hits and hits[0]["source"] == "fts"
    assert "amber" in service.active_recall("amber")
    assert len(service.active_recall("amber", cap=7)) == 7
    journal.vector_store = archive
    assert service.active_recall("amber") == ""
    memory_service._services[id(journal)] = MemoryService.over_vector_store(None)
    replacement = service_for(journal)
    assert replacement is not service and replacement.provider is journal
    assert replacement.fts_fallback_search("amber")


def test_projection_and_archive_blocks_keep_order_and_fences(local_memory):
    journal, archive = local_memory
    journal.write_preferences("# User Preferences\n\n- Prefer amber terminology.\n")
    archive.set_semantic("pref.editor", "helix", 1.0, "user_explicit")
    journal.vector_store = archive
    service = MemoryService(journal)
    args = dict(prefs_cap=4000, projects_cap=6000, history_cap=25000)
    projection = journal.render_markdown_context(**args)
    for manifest in (True, False):
        expected = projection + [
            (
                archive.get_l1_manifest()
                if manifest
                else archive.get_semantic_context(query_text="", cap=12000)
            )
        ]
        expected = (
            "[Memory — persistent user profile and recent activity log.\n"
            "Preferences are rules you MUST follow. Projects give current work context.\n"
            "History is a factual record — do NOT re-execute past actions.]\n"
            + "\n\n".join(expected)
            + "\n[End of memory]\n\n"
        )
        assert service.get_context(l1_manifest=manifest) == expected
    assert service.l1_manifest(cap=600, limit=1) == archive.get_l1_manifest(
        cap=600, limit=1
    )


def test_episode_queries_keep_filters_citations_and_delete_audit(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    assert archive.write_episodic(
        "The amber launch checklist includes the delivery milestones.",
        tags=["release"],
        source="user_explicit",
    )
    episodes = service.episodic_list(tag_filter=["release"])
    assert len(episodes) == 1
    assert service.episodic_list(tag_filter=["other"]) == []
    hits = service.search_episodic(
        query_text="amber launch", mmr=False, tag_filter=["release"]
    )
    assert hits and hits[0]["id"] == episodes[0]["id"]
    citations = []
    text = service.episodic_context("amber launch", citations_out=citations)
    assert "amber" in text and citations
    assert service.delete_episodic(episodes[0]["id"], source="operator")
    assert service.episodic_list() == []
    assert any(event["source"] == "operator" for event in archive.get_events())


def test_workspace_lesson_projection_is_exact_and_global_is_visible(
    local_memory, tmp_path
):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    reference = str(tmp_path / "local")
    archive.write_lesson(
        "Use checklist amber for deployments.", scope=MemoryScope.GLOBAL
    )
    archive.write_lesson(
        "Run delta migrations in maintenance windows.",
        scope=MemoryScope.WORKSPACE,
        scope_ref=reference,
    )
    assert "checklist amber" in service.lessons_context()
    assert "delta migrations" not in service.lessons_context()
    assert "delta migrations" in service.lessons_context(reference)
    assert "delta migrations" not in service.lessons_context(str(tmp_path / "other"))


def test_graph_views_alias_invalidation_and_explicit_backfill(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    archive.set_semantic(
        "project.notes", "Dana Quinn maintains the amber release.", 1.0, "user_explicit"
    )
    archive.graph_enabled = True
    old_index = archive.alias_index
    identity = service.graph_add_entity("Dana Quinn", "person", aliases=["@dquinn"])
    assert archive.alias_index is not old_index
    assert service.graph_record_links("sem:project.notes") == []
    service.graph_backfill()
    links = service.graph_record_links("sem:project.notes")
    assert links and links[0]["entity_name"] == "Dana Quinn"
    assert service.graph_backlinks(identity) == archive.graph.backlinks(identity)
    assert service.resolve_entities("Ask @dquinn") == [
        dict(id=identity, name="Dana Quinn", entity_type="person", aliases=["@dquinn"])
    ]
    assert service.graph_entities()[0]["inbound_count"] >= 1
    assert service.graph_summary()["entities"] == 1
    assert service.entity_graph() == archive.graph.entity_graph()
    assert service.graph_recall_evidence("Dana Quinn") == archive.graph.recall_evidence(
        "Dana Quinn", index=archive.alias_index
    )
    for ref in ("project.notes", "unknown:project.notes", "sem:"):
        assert service.graph_record_links(ref) == []


def test_proposal_accept_reject_updates_the_shared_alias_index(local_memory):
    _, archive = local_memory
    archive.graph_enabled = True
    service = MemoryService.over_vector_store(archive)
    for n in range(4):
        archive.graph.tally_proposal("Nora Price", f"pref.nora{n}")
        archive.graph.tally_proposal("Liam Finch", f"pref.liam{n}")
    assert {item["name"] for item in service.graph_proposals()} == {
        "Nora Price",
        "Liam Finch",
    }
    original = archive.alias_index
    identity = service.graph_accept_proposal("Nora Price", "person")
    assert archive.alias_index is not original
    assert service.resolve_entities("Nora Price")[0]["id"] == identity
    assert service.graph_reject_proposal("Liam Finch")
    assert service.graph_proposals() == []


def test_slots_show_lazy_builtin_and_persist_human_tombstones(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    initial = service.slots()
    assert [slot["name"] for slot in initial] == list(BLOCK_ORDER)
    assert all(not slot["materialized"] and slot["live_count"] == 0 for slot in initial)
    service.slot_append("project_glossary", "Amber means the release branch.")
    materialized = service.slots()[-1]
    assert materialized["name"] == "project_glossary"
    assert materialized["materialized"] and not materialized["builtin"]
    assert materialized["live_count"] == 1
    assert service.slot_tombstone("project_glossary", "Amber means the release branch.")
    service.slot_append("project_glossary", "Amber means the release branch.")
    assert service.slots()[-1]["live_count"] == 0
    with pytest.raises(SlotCapExceeded):
        service.slot_append("persona", "x" * 1000)


def test_vault_edit_is_scanned_owned_and_undoable(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    archive.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    assert service.apply_vault_edit("pref.editor", "helix") == (True, "applied")
    event = next(
        item for item in archive.get_events() if item["source"] == "vault_edit"
    )
    assert archive.undo_event(event["id"])[0]
    assert json.loads(archive.get_semantic("pref.editor")["value_json"]) == "vim"
    assert service.apply_vault_edit("pref.editor", "helix \u202e is the editor") == (
        False,
        "blocked: injection/steering payload in the edited page",
    )
    accepted, detail = service.apply_vault_edit("system.owner", "operator")
    assert not accepted and detail.startswith("rejected (")
    archive.db.execute(
        "CREATE TRIGGER reject_vault BEFORE UPDATE ON semantic_memory BEGIN SELECT RAISE(ABORT, 'vault write refused'); END"
    )
    accepted, detail = service.apply_vault_edit("pref.editor", "emacs")
    assert not accepted and detail.startswith("refused:")
    archive.db.rollback()


def test_volunteering_deduplicates_records_and_captures_recall_baseline(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    archive.set_semantic(
        "project.notes",
        {"rule": "Amber rollout follows the checklist."},
        1.0,
        "user_explicit",
    )
    archive.graph_enabled = True
    candidates = []
    for name in ("Dana Quinn", "Nora Price"):
        identity = service.graph_add_entity(name, "person")
        archive.graph.add_link(
            from_kind="semantic",
            from_ref="project.notes",
            to_entity=identity,
            link_type="mentions",
        )
        candidates.append(Candidate(identity, name, "exact_name", 0.9))
    archive.record_recall(["project.notes"])
    block, volunteered = service._volunteer_for(
        archive, candidates, cap=5, session_key="session-local", log_events=True
    )
    assert block.count("Amber rollout") == 1 and len(volunteered) == 1
    event = archive.db.execute("SELECT * FROM mem_volunteer_events").fetchone()
    assert event["session_key"] == "session-local" and event["recall_at_volunteer"] == 1
    assert service.volunteer_precision()["overall"]["used"] == 0
    archive.record_recall(["project.notes"])
    assert service.volunteer_precision()["overall"]["used"] == 1
    assert service._volunteer_for(
        archive, candidates, cap=0, session_key="", log_events=True
    ) == ("", [])
    assert service.push_context(["Dana Quinn", "Dana Quinn"], log_events=False)[1]
    assert (
        archive.db.execute("SELECT COUNT(*) FROM mem_volunteer_events").fetchone()[0]
        == 1
    )


def test_volunteer_budget_omission_keeps_existing_event_semantics(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    archive.set_semantic("project.long_note", "amber " * 240, 1.0, "user_explicit")
    archive.graph_enabled = True
    identity = service.graph_add_entity("Dana Quinn", "person")
    archive.graph.add_link(
        from_kind="semantic",
        from_ref="project.long_note",
        to_entity=identity,
        link_type="mentions",
    )
    candidate = Candidate(identity, "Dana Quinn", "exact_name", 0.9)
    block, volunteered = service._volunteer_for(
        archive, [candidate], cap=1, session_key="", log_events=True
    )
    assert block == "" and len(volunteered) == 1
    assert service.volunteer_precision()["overall"]["n"] == 1


def test_graph_disabled_returns_no_data_or_mutations(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    assert not service.has_graph
    assert service.graph_add_entity("Dana Quinn", "person") == ""
    assert service.graph_accept_proposal("Dana Quinn", "person") == ""
    assert not service.graph_reject_proposal("Dana Quinn")
    assert (
        service.graph_summary()
        == service.graph_seed()
        == service.graph_backfill()
        == {}
    )
    assert (
        service.graph_entities()
        == service.graph_proposals()
        == service.graph_backlinks("x")
        == []
    )
    assert service.entity_graph() == {"nodes": [], "edges": []}
    assert service.resolve_entities("Dana Quinn") == []
    assert service.graph_recall_evidence("Dana Quinn") == {}
    assert service.push_context(["Dana Quinn"]) == ("", [])
    assert service.topology_block() == "" and service.refresh_topology() == 0
    assert service.prune_volunteer_events() == 0
    assert service.volunteer_precision() == {
        "arms": {},
        "overall": {"n": 0, "used": 0, "precision": 0.0},
    }


def test_graph_database_failures_degrade_optional_reads_only(local_memory):
    _, archive = local_memory
    archive.graph_enabled = True
    service = MemoryService.over_vector_store(archive)
    service.graph_add_entity("Dana Quinn", "person")
    archive.db.execute("DROP TABLE mem_entities")
    archive.db.execute("DROP TABLE mem_volunteer_events")
    archive.db.commit()
    assert service.resolve_entities("Dana Quinn") == []
    assert service.graph_recall_evidence("Dana Quinn") == {}
    assert service.push_context(["Dana Quinn"]) == ("", [])
    assert service.volunteer_precision()["overall"]["n"] == 0
    assert service.prune_volunteer_events() == 0
    with pytest.raises(sqlite3.Error):
        service.graph_entities()
    with pytest.raises(ValueError):
        service.push_context(["Dana Quinn"], min_confidence="invalid")


def test_seed_is_explicit_about_identity_and_does_not_backfill(local_memory):
    _, archive = local_memory
    archive.set_semantic("project.name", "Amber", 1.0, "user_explicit")
    archive.graph_enabled = True
    service = MemoryService.over_vector_store(archive)
    before = archive.alias_index
    seeded = service.graph_seed()
    assert seeded == {"from_facts": 1, "from_knowledge": 0}
    assert archive.alias_index is not before
    assert service.resolve_entities("Amber")[0]["entity_type"] == "project"
    assert service.graph_record_links("sem:project.name") == []


def test_topology_live_toggle_and_refresh_use_persisted_communities(local_memory):
    _, archive = local_memory
    archive.graph_enabled = True
    service = MemoryService.over_vector_store(archive)
    for index, name in enumerate(("Amber", "Cobalt", "Delta", "Orchid")):
        identity = service.graph_add_entity(name, "project")
        archive.db.execute(
            "INSERT INTO mem_link_stats (entity_id, community) VALUES (?, ?)",
            (identity, index // 2),
        )
    archive.db.commit()
    assert service.topology_block() == ""
    config = AppConfig.load()
    config.memory.graph_topology_in_context = True
    config.save()
    text = service.topology_block()
    assert "Amber" in text and "Orchid" in text
    assert text in service.get_context(l1_manifest=True)
    assert service.refresh_topology() == 4
    archive.db.execute("DROP TABLE mem_entities")
    archive.db.commit()
    assert service.topology_block() == ""


def test_health_sweep_reports_actual_stale_record(local_memory):
    _, archive = local_memory
    service = MemoryService.over_vector_store(archive)
    archive.set_semantic("pref.editor", "vim", 1.0, "user_explicit")
    archive.db.execute(
        "UPDATE semantic_memory SET updated_at='2000-01-01T00:00:00+00:00' WHERE key='pref.editor'"
    )
    archive.db.commit()
    report = service.lint()
    assert report["flag_count"] == len(report["flags"])
    assert any(
        flag["check"] == "stale" and flag["key"] == "pref.editor"
        for flag in report["flags"]
    )
