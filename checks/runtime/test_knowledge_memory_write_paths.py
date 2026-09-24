import json
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition import after_turn_review, context_engine, memory_slots
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.preference_facets import upsert_facet
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.extensions.apps.manager import app_dir
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import (
    ContextEngineTypeHandler,
    ProviderRegistry,
)
from gideon.extensions.skills import proposals
from gideon.integrations.action_providers.base import ActionContext
from gideon.integrations.action_providers.knowledge_retrieve_provider import (
    KnowledgeRetrieveActionProvider,
)
from gideon.interfaces.dashboard.handlers.memory import _get_provider, api_memory_facet
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    (tmp_path / "config.json").write_text('{"providers": []}')
    (tmp_path / "active_models.json").write_text("{}")
    yield tmp_path
    context_engine.set_engine(None)


@pytest.mark.asyncio
async def test_retrieve_tags_filter_real_persisted_rows_and_overview():
    store = KnowledgeStore(str(knowledge_db_path()))
    try:
        wanted = store.create_typed_item(
            item_type="note",
            title="Needle relevant",
            content="needle",
            tags=["chosen", "both"],
        )
        store.create_typed_item(
            item_type="note", title="Needle wrong", content="needle", tags=["other"]
        )
        overview = store.create_typed_item(
            item_type="note", title="Needle", content="needle", tags=["other"]
        )
        store.set_item_identity(overview, kind="overview")
        result = await KnowledgeRetrieveActionProvider().execute(
            {"query": "needle", "mode": "fts", "filters": {"tags": ["chosen", "both"]}},
            ActionContext("workflow_node"),
        )
        assert result.success
        assert [row["item_id"] for row in json.loads(result.stdout)["items"]] == [
            wanted
        ]
    finally:
        store.close()


@pytest.mark.asyncio
async def test_invalid_tags_are_reported_before_search():
    result = await KnowledgeRetrieveActionProvider().execute(
        {"query": "needle", "filters": {"tags": [{}]}}, ActionContext("workflow_node")
    )
    assert not result.success and "filters.tags" in result.error


def test_glossary_capture_from_review_preserves_human_tombstone(home):
    store = SemanticArchive(db_path=home / "memory.db", embedding_dim=3)
    store.init()
    service = MemoryService.over_vector_store(store)
    try:
        after_turn_review.run_after_turn_review(
            service=service,
            user_message="SLO means service level objective",
            assistant_text="",
            correction=False,
        )
        lines = memory_slots.live_lines(memory_slots.load(store, "glossary"))
        assert len(lines) == 1 and lines[0].text == "SLO = service level objective"
        assert service.slot_tombstone("glossary", lines[0].text)
        after_turn_review.run_after_turn_review(
            service=service,
            user_message="SLO means service level objective",
            assistant_text="",
            correction=False,
        )
        assert memory_slots.live_lines(memory_slots.load(store, "glossary")) == []
    finally:
        store.close()


@pytest.mark.asyncio
async def test_facet_http_controls_persist_and_reject_nonfacet_keys():
    state = ConsoleState(ConversationDirectory(AppConfig.load()), time.time())
    store = _get_provider(state)
    key = upsert_facet(store, "style", "Use short paragraphs", cue="explicit")
    app = web.Application()
    app["state"] = state
    app.router.add_post("/api/memory/facets", api_memory_facet)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        for pinned in (True, False):
            reply = await client.post(
                "/api/memory/facets",
                json={"key": key, "action": "pin", "pinned": pinned},
            )
            assert reply.status == 200
            assert json.loads(store.get_semantic(key)["value_json"])["pinned"] is pinned
        reply = await client.post(
            "/api/memory/facets", json={"key": key, "action": "forget"}
        )
        assert reply.status == 200
        assert json.loads(store.get_semantic(key)["value_json"])["forgotten"] is True
        reply = await client.post(
            "/api/memory/facets", json={"key": "user.name", "action": "forget"}
        )
        assert reply.status == 400
        reply = await client.post(
            "/api/memory/facets", json={"key": "pref.facet.missing", "action": "forget"}
        )
        assert reply.status == 404
    finally:
        await client.close()
        store.close()


def test_installed_context_engine_factory_reaches_live_registry_and_uninstall():
    root = app_dir("context-local")
    root.mkdir(parents=True)
    (root / "engine.py").write_text(
        "from gideon.sdk.context import DefaultContextEngine\ndef create(config):\n    return DefaultContextEngine()\n"
    )
    manifest = AppManifest.from_dict(
        {
            "name": "context-local",
            "version": "1.0.0",
            "description": "Local context",
            "provider": {"type": "context_engine", "implementation": "engine:create"},
        }
    )
    registry = ProviderRegistry()
    registry.register_type_handler("context_engine", ContextEngineTypeHandler())
    registry.register(manifest)
    assert registry.enable("context-local")
    active = registry.get("context-local").provider_instance
    assert context_engine.get_engine() is active
    assert registry.disable("context-local")
    assert context_engine.get_engine() is not active


@pytest.mark.parametrize("accepted", [False, True])
def test_skill_review_records_feedback_with_synthesis_identity(home, accepted):
    proposal = proposals.enqueue(
        slug="write-checklist",
        description="Review a local checklist",
        triggers="checklist",
        procedure_md="1. Read the checklist.\n2. Record the result.",
        session_key="local-session",
        created_at="2026-09-24T00:00:00+00:00",
    )
    assert proposal is not None
    if accepted:
        assert proposals.accept(proposal.id).name == "auto/write-checklist"
    else:
        assert proposals.reject(proposal.id)
    records = [
        json.loads(line) for line in (home / "feedback.jsonl").read_text().splitlines()
    ]
    assert len(records) == 1
    assert records[0]["producer_kind"] == "skill_synthesis"
    assert records[0]["producer_id"] == "auto/write-checklist"
    assert records[0]["verdict"] == ("up" if accepted else "down")
