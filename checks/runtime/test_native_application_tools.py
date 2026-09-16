"""Application tool paths backed by real temporary stores and service state."""

import pytest

from gideon.engine.agents.native.builtin_tools import NativeBuiltinToolProvider


@pytest.fixture
def library(tmp_path, monkeypatch):
    import gideon.cognition.knowledge as knowledge
    from gideon.cognition.knowledge.store import KnowledgeStore

    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    store = KnowledgeStore(str(tmp_path / "knowledge.db"))
    monkeypatch.setattr(knowledge, "_store", store)
    yield store
    store.close()


@pytest.mark.asyncio
async def test_structural_tool_follows_persisted_dependency_edges(library, tmp_path):
    first = library.create_typed_item(item_type="note", title="Release", content="ship")
    second = library.create_typed_item(
        item_type="note", title="Approval", content="approve"
    )
    library.db.execute(
        "INSERT INTO item_relations (source_item_id, target_item_id, relation_type, confidence, provenance, created_at) VALUES (?, ?, 'depends_on', 1, 'extracted', '2026-01-01T00:00:00')",
        (first, second),
    )
    library.db.commit()
    result = await NativeBuiltinToolProvider(tmp_path).invoke(
        "knowledge_structural", {"verb": "depends_on", "origin": first}
    )
    assert result.success, result.error
    assert "Approval" in result.output and second in result.output
    assert "depends_on" in result.output


@pytest.mark.asyncio
async def test_bookmark_dedup_and_url_validation_use_real_library(library, tmp_path):
    saved = library.create_typed_item(
        item_type="bookmark", title="Manual", url="https://example.org/manual"
    )
    library.db.commit()
    provider = NativeBuiltinToolProvider(tmp_path)
    duplicate = await provider.invoke(
        "knowledge_create", {"type": "bookmark", "url": "https://example.org/manual"}
    )
    assert duplicate.success and duplicate.output.endswith(saved)
    assert "already saved" in duplicate.output
    bad = await provider.invoke(
        "knowledge_update", {"id": saved, "url": "javascript:alert(1)"}
    )
    assert not bad.success and "http(s)" in bad.error
    assert library.get_item(saved)["url"] == "https://example.org/manual"


@pytest.mark.asyncio
async def test_decision_tools_persist_review_deferral_and_resolution(library, tmp_path):
    from gideon.cognition.decisions import list_decisions

    provider = NativeBuiltinToolProvider(tmp_path)
    logged = await provider.invoke(
        "log_decision",
        {
            "summary": "Ship the bounded rewrite",
            "expectation": "All inherited behavior survives",
            "confidence": 0.8,
            "domain": "technical",
            "review_horizon": "2077-01-01T00:00:00",
        },
    )
    assert logged.success, logged.error
    rows = list_decisions(store=library)
    assert len(rows) == 1
    identifier = rows[0]["id"]
    listed = await provider.invoke("decision_list", {"status": "pending"})
    assert listed.success and identifier in listed.output
    deferred = await provider.invoke(
        "decision_resolve",
        {"id": identifier, "outcome": "Still gathering evidence", "grade": "too_early"},
    )
    assert deferred.success and "rescheduled" in deferred.output
    resolved = await provider.invoke(
        "decision_resolve",
        {
            "id": identifier,
            "outcome": "Behavior remained intact",
            "grade": "as_expected",
        },
    )
    assert resolved.success and "Resolved" in resolved.output
    assert list_decisions(store=library, status="resolved")[0]["id"] == identifier


@pytest.mark.asyncio
async def test_project_run_adapters_create_list_inspect_and_refuse_offline_start(
    library, tmp_path
):
    from gideon.automation.loop import store
    from gideon.integrations.inbox_providers import native_source

    previous = native_source.get_dashboard_state()
    native_source.set_dashboard_state(None)
    try:
        provider = NativeBuiltinToolProvider(tmp_path)
        created = await provider.invoke(
            "project_run_create",
            {
                "kind": "code",
                "task": "Add a health endpoint to the service",
                "project_kind": "greenfield",
                "entry_stage": "implementation",
                "stage_plan": [
                    {
                        "stage": "implementation",
                        "title": "Implement",
                        "objective": "Add the endpoint",
                    }
                ],
            },
        )
        assert created.success, created.error
        identifier = next(row.id for row in store.list_all() if row.kind == "code")
        listed = await provider.invoke("project_run_list", {"kind": "code"})
        status = await provider.invoke("project_run_status", {"project_id": identifier})
        start = await provider.invoke("project_run_start", {"project_id": identifier})
        assert listed.success and identifier in listed.output
        assert status.success and identifier in status.output
        assert not start.success and "unavailable" in start.error.lower()
    finally:
        native_source.set_dashboard_state(previous)


@pytest.mark.asyncio
async def test_inbox_tool_writes_replyable_item_with_constructor_identity(
    library, tmp_path
):
    from gideon.core.config.loader import AppConfig
    from gideon.engine.session import ConversationDirectory
    from gideon.integrations.inbox import InboxStore
    from gideon.integrations.inbox_providers import native_source
    from gideon.interfaces.dashboard.state import ConsoleState

    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._inbox_store = InboxStore(path=tmp_path / "inbox.json")
    state._inbox_store.load()
    previous = native_source.get_dashboard_state()
    native_source.set_dashboard_state(state)
    try:
        provider = NativeBuiltinToolProvider(
            tmp_path, agent="researcher", session_key="chat:application"
        )
        result = await provider.invoke(
            "post_to_inbox",
            {
                "message": "Which direction?",
                "kind": "question",
                "context": "Two options",
            },
        )
        assert result.success, result.error
        reloaded = InboxStore(path=tmp_path / "inbox.json")
        reloaded.load()
        item = next(iter(reloaded.items.values()))
        assert (
            item.sender_name == "researcher" and item.reply_target == "chat:application"
        )
        assert item.can_reply and item.context_summary == "Two options"
    finally:
        native_source.set_dashboard_state(previous)
