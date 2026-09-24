"""Retrieval settings survive persistence and govern real runtime consumers."""

import json
import logging
import time

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from test_skill_progressive_disclosure import _builder_with_skills

from gideon.cognition.knowledge.embedder import UnifiedEmbedder
from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.core.config.loader import AppConfig, SkillsConfig
from gideon.interfaces.dashboard.handlers.knowledge import search_for_context
from gideon.interfaces.dashboard.state import ConsoleState


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text("{}")
    return tmp_path


def test_defaults_and_roundtrip(home):
    config = AppConfig.load()
    assert config.knowledge.fetch_max_tokens == 4096
    assert config.knowledge.fetch_top_n == 3
    assert config.skills.progressive_disclosure_threshold == 2
    config.knowledge.fetch_max_tokens = 1000
    config.knowledge.fetch_top_n = 7
    config.save()
    loaded = AppConfig.load()
    assert loaded.knowledge.fetch_max_tokens == 1000
    assert loaded.knowledge.fetch_top_n == 7


@pytest.mark.parametrize(
    "limit,threshold,expected", [(3, 8, 2), (1, 2, 0), (5, 0, 0), (5, 2, 2)]
)
def test_disclosure_clamp_logs(limit, threshold, expected, caplog):
    with caplog.at_level(logging.WARNING):
        config = SkillsConfig(
            max_triggered=limit, progressive_disclosure_threshold=threshold
        )
    assert config.progressive_disclosure_threshold == expected
    assert ("exceeds max_triggered - 1" in caplog.text) == (threshold > limit - 1)


def test_saved_disclosure_settings_control_prompt(home):
    builder = _builder_with_skills(home, 3)
    message, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "INDEX only" in message
    assert "BODY-0" not in message
    (home / "config.json").write_text(
        json.dumps({"skills": {"progressive_disclosure_threshold": 0}})
    )
    message, _ = builder.build_message("deploy widget now", is_new_session=False)
    assert "INDEX only" not in message
    assert "BODY-0" in message


@pytest.mark.asyncio
async def test_saved_fetch_settings_control_context_response(home):
    store = KnowledgeStore(str(home / "knowledge.db"))
    for n in range(4):
        store.create_typed_item(
            item_type="note", title=f"Needle {n}", content="Needle " * 100
        )
    state = ConsoleState(sessions=None, start_time=time.time())
    state._knowledge_store = store
    app = web.Application()
    app["state"] = state
    app["knowledge_embedder"] = UnifiedEmbedder(None)
    app.router.add_get("/search", search_for_context)
    try:
        async with TestClient(TestServer(app)) as client:
            response = await client.get("/search?q=Needle")
            assert response.status == 200
            payload = await response.json()
            assert len(payload["results"]) == 3
            assert payload["max_tokens"] == 4096
            config = AppConfig.load()
            config.knowledge.fetch_top_n = 2
            config.knowledge.fetch_max_tokens = 10
            config.save()
            response = await client.get("/search?q=Needle")
            assert response.status == 200
            payload = await response.json()
            assert len(payload["results"]) == 2
            assert payload["max_tokens"] == 10
            assert payload["total_tokens"] <= 10
            response = await client.get("/search?q=Needle&limit=1&max_tokens=4")
            payload = await response.json()
            assert len(payload["results"]) == 1
            assert payload["max_tokens"] == 4
            assert payload["total_tokens"] <= 4
    finally:
        store.close()
