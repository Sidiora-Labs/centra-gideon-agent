"""The per-correction route deletes only its selected learned correction."""

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_delete_correction_preserves_neighboring_vocabulary(tmp_path, monkeypatch):
    from gideon.cognition.lexicon import service
    from gideon.cognition.lexicon.service import LexiconService
    from gideon.cognition.lexicon.store import LexiconStore
    from gideon.cognition.lexicon.handlers import register_lexicon_routes

    store = LexiconStore(str(tmp_path / "lexicon.db"))
    first = store.upsert_correction("Niro", "Nero")
    second = store.upsert_correction("Gideon", "Gideon")
    store.upsert_term("term-1", "Kubernetes", source="manual")
    monkeypatch.setattr(service, "_service", LexiconService(store))

    app = web.Application()
    register_lexicon_routes(app)
    async with TestClient(TestServer(app)) as client:
        response = await client.delete(f"/api/lexicon/corrections/{first.id}")
        assert response.status == 200
        assert await response.json() == {"ok": True}

        missing = await client.delete(f"/api/lexicon/corrections/{first.id}")
        assert missing.status == 404

    assert [row.id for row in store.list_corrections()] == [second.id]
    assert [row.canonical for row in store.list_terms()] == ["Kubernetes"]
