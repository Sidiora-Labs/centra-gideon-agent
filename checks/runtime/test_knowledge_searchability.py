"""The ``unsearchable`` terminal state: assigned with a typed reason, surfaced, cleared.

The condition this file exists for is invisible by construction. An item that finished
ingesting and an item a search can return are two different facts, and before this state
existed they produced identical rows: ``processing_status='done'``, no error, and then
nothing in the results. A test that asserted "the ingest returned done" would have passed
on every one of the three broken cases below.

So every test here asserts the EFFECT and then the DIAGNOSIS separately — that the item is
genuinely unreachable (the real retriever finds nothing) AND that the recorded reason names
the right missing prerequisite. The three reasons stack in real life (an item with no text
has no vector either), so a test that only checked "some reason was recorded" would pass
with the ladder in any order and send the user after the wrong remedy.

Everything runs against a real ``KnowledgeStore`` and the real ``ingest_item`` under a tmp
home; the index is broken the way the store itself breaks it (FTS5's ``'delete'`` command,
a dropped virtual table), never by stubbing the assessment.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from gideon.cognition.knowledge import searchability
from gideon.cognition.knowledge.pipeline.runner import ingest_item
from gideon.cognition.knowledge.retrieval import HybridRetriever
from gideon.cognition.knowledge.store import KnowledgeStore, knowledge_db_path


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    return tmp_path / "home"


@pytest.fixture()
def store():
    return KnowledgeStore(str(knowledge_db_path()))


class _VectorEmbedder:
    """A real embedder shape that yields a vector, so the embed stage has one to write."""

    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def embed_for_item(title, summary, content):
        return [1.0, 0.0, 0.0, 0.0]

    @staticmethod
    def embed(text):
        return [1.0, 0.0, 0.0, 0.0]


class _NoVectorEmbedder:
    """A configured provider that answers with nothing — present, so the reason ladder must
    NOT blame a missing provider, yet leaving the item without a vector."""

    @staticmethod
    def is_available() -> bool:
        return True

    @staticmethod
    def embed_for_item(title, summary, content):
        return []

    @staticmethod
    def embed(text):
        return []


def _unindex(store, item_id: str) -> None:
    """Remove the item from the FTS index through FTS5's own ``'delete'`` command.

    The store documents this as the ONLY correct removal on an external-content table — a
    plain ``DELETE FROM items_fts`` is a silent no-op there — so this reproduces the real
    shape of an item whose index write did not land, rather than inventing one.
    """
    for rowid, title, content, tags in store._fts_snapshot([item_id]):
        store.db.execute(
            "INSERT INTO items_fts (items_fts, rowid, title, content, tags) "
            "VALUES ('delete', ?, ?, ?, ?)",
            (rowid, title, content, tags),
        )
    store.db.commit()


def _reason(store, item_id: str) -> str:
    return searchability.reason_of(store.get_item(item_id) or {})


def _finds(store, item_id: str, query: str) -> bool:
    return any(
        hit["id"] == item_id for hit in HybridRetriever(store).search(query, limit=20)
    )


class TestTheThreeReasons:
    def test_an_ingest_that_extracted_nothing_names_the_missing_content(self, store):
        item_id = store.create_typed_item(item_type="note", title="", content="")
        store.db.commit()

        status = _run(ingest_item(store, item_id))

        assert status == searchability.STATE_UNSEARCHABLE
        assert store.get_item(item_id)["processing_status"] == "unsearchable"
        assert _reason(store, item_id) == searchability.REASON_NO_CONTENT

    def test_an_unindexed_item_with_no_provider_names_the_missing_embedder(self, store):
        """Text that a keyword index cannot return and no provider to give it a vector.

        The remedy here is a model, not a re-ingest — which is exactly what distinguishes it
        from the case below, where a provider IS configured and the index is still the
        problem."""
        item_id = store.create_typed_item(
            item_type="note",
            title="Kestrel field notes",
            content="a falcon that hunts the open plains",
        )
        store.db.commit()
        _unindex(store, item_id)

        status = _run(ingest_item(store, item_id, embedder=None))

        assert not _finds(store, item_id, "kestrel")
        assert status == searchability.STATE_UNSEARCHABLE
        assert _reason(store, item_id) == searchability.REASON_NO_EMBEDDING_PROVIDER

    def test_a_configured_provider_that_writes_no_vector_names_the_index(self, store):
        """Same absence of a vector, different cause: a provider ran. Blaming the provider
        here would send the user to connect a model they already have."""
        item_id = store.create_typed_item(
            item_type="note",
            title="Kestrel field notes",
            content="a falcon that hunts the open plains",
        )
        store.db.commit()
        _unindex(store, item_id)

        status = _run(ingest_item(store, item_id, embedder=_NoVectorEmbedder()))

        assert status == searchability.STATE_UNSEARCHABLE
        assert _reason(store, item_id) == searchability.REASON_NO_INDEX

    def test_an_unreadable_index_names_the_index_even_with_no_provider(self, store):
        """The index could not be CONSULTED at all — a different fact from an index that
        was consulted and did not hold the row, and the one that outranks a missing
        provider because no embedding model repairs a dropped table."""
        item_id = store.create_typed_item(
            item_type="note",
            title="Kestrel field notes",
            content="a falcon that hunts the open plains",
        )
        store.db.commit()
        store.db.execute("DROP TABLE items_fts")
        store.db.commit()

        status = _run(ingest_item(store, item_id, embedder=None))

        assert status == searchability.STATE_UNSEARCHABLE
        assert _reason(store, item_id) == searchability.REASON_NO_INDEX

    def test_a_findable_item_is_never_marked_unsearchable(self, store):
        """The control. Without it every assertion above is satisfied by a pipeline that
        marks everything unsearchable."""
        item_id = store.create_typed_item(
            item_type="note",
            title="Kestrel field notes",
            content="a falcon that hunts the open plains",
        )
        store.db.commit()

        status = _run(ingest_item(store, item_id, embedder=None))

        assert _finds(store, item_id, "kestrel")
        assert status == "done"
        assert _reason(store, item_id) == ""

    def test_a_vector_alone_keeps_an_unindexed_item_searchable(self, store):
        """One live arm is enough. An item the keyword index lost but the vector arm can
        still score is not unsearchable, and calling it so would be a false alarm."""
        item_id = store.create_typed_item(
            item_type="note",
            title="Kestrel field notes",
            content="a falcon that hunts the open plains",
        )
        store.db.commit()
        _unindex(store, item_id)

        status = _run(ingest_item(store, item_id, embedder=_VectorEmbedder()))

        assert status == "done"
        assert _reason(store, item_id) == ""


class TestTheReasonIsSurfaced:
    async def test_the_search_api_reports_the_reason(self, store):
        """Through the REAL ``GET /api/knowledge/items?q=`` handler. The unsearchable item
        can never appear among the results — that is what unsearchable means — so the only
        place the user can learn why is the response's own diagnostics."""
        from gideon.interfaces.dashboard.handlers.knowledge import list_items

        store.create_typed_item(
            item_type="note", title="Kestrel field notes", content="a falcon"
        )
        lost = store.create_typed_item(item_type="note", title="", content="")
        store.db.commit()
        await ingest_item(store, lost)

        class _State:
            knowledge_store = store

        class _NoEmbedder:
            @staticmethod
            def is_available() -> bool:
                return False

        class _Req:
            query = {"q": "kestrel"}
            app = {"state": _State(), "knowledge_embedder": _NoEmbedder()}

        payload = json.loads((await list_items(_Req())).body.decode())

        assert lost not in [item["id"] for item in payload["items"]]
        assert payload["unsearchable"]["count"] == 1
        assert payload["unsearchable"]["reasons"] == {
            searchability.REASON_NO_CONTENT: 1
        }

    def test_the_doctor_probe_is_registered(self):
        from gideon.operations.resilience.doctor import all_probes

        assert "knowledge.searchability" in {probe.id for probe in all_probes()}

    def test_the_doctor_probe_reports_the_reason(self, store, home):
        from gideon.operations.resilience.doctor import DoctorContext, all_probes

        store.create_typed_item(
            item_type="note", title="Kestrel field notes", content="a falcon"
        )
        lost = store.create_typed_item(item_type="note", title="", content="")
        store.db.commit()
        _run(ingest_item(store, lost))
        store.close()

        probe = next(p for p in all_probes() if p.id == "knowledge.searchability")
        result = _run(probe.run(DoctorContext(home=home)))

        assert result.ok, "unsearchable content is a coverage gap, never an outage"
        assert result.evidence["unsearchable"] == 1
        assert result.evidence["reasons"] == {searchability.REASON_NO_CONTENT: 1}
        assert result.evidence["degraded"] is True
        assert "cannot be found" in result.detail
        assert (
            result.evidence["remedy"]
            == searchability.REASON_REMEDY[searchability.REASON_NO_CONTENT]
        ), "the reason is only useful if it names what to DO about it"

    def test_the_doctor_probe_is_quiet_on_a_healthy_library(self, store, home):
        from gideon.operations.resilience.doctor import DoctorContext, all_probes

        item_id = store.create_typed_item(
            item_type="note", title="Kestrel field notes", content="a falcon"
        )
        store.db.commit()
        _run(ingest_item(store, item_id))
        store.close()

        probe = next(p for p in all_probes() if p.id == "knowledge.searchability")
        result = _run(probe.run(DoctorContext(home=home)))

        assert result.evidence["unsearchable"] == 0
        assert "degraded" not in result.evidence
        assert "are searchable" in result.detail


class TestTheStateIsCleared:
    def test_a_successful_reingest_clears_state_and_reason(self, store):
        item_id = store.create_typed_item(item_type="note", title="", content="")
        store.db.commit()
        assert _run(ingest_item(store, item_id)) == searchability.STATE_UNSEARCHABLE

        store.update_item(
            item_id,
            title="Kestrel field notes",
            content="a falcon that hunts the open plains",
        )
        store.db.commit()

        status = _run(ingest_item(store, item_id))

        assert status == "done"
        assert store.get_item(item_id)["processing_status"] == "done"
        assert _reason(store, item_id) == ""
        assert searchability.METADATA_KEY not in (
            store.get_item(item_id).get("file_metadata") or {}
        )
        assert _finds(store, item_id, "kestrel")

    def test_the_cleared_item_leaves_the_diagnostics(self, store):
        item_id = store.create_typed_item(item_type="note", title="", content="")
        store.db.commit()
        _run(ingest_item(store, item_id))
        assert searchability.summary(store)["count"] == 1

        store.update_item(item_id, title="Kestrel", content="a falcon")
        store.db.commit()
        _run(ingest_item(store, item_id))

        assert searchability.summary(store) == {"count": 0, "reasons": {}}


class TestTheAssessmentItself:
    def test_a_usable_index_that_lost_the_row_is_not_an_unusable_index(self, store):
        """``keyword_reach`` must tell "the index said no" from "the index could not be
        asked". They carry different reasons and different remedies, and an
        external-content FTS5 table answers a bare rowid lookup out of the CONTENT table —
        so a check written the obvious way reports a hit for a row the index never took.
        """
        item_id = store.create_typed_item(
            item_type="note", title="Kestrel", content="a falcon"
        )
        store.db.commit()
        assert searchability.keyword_reach(store, item_id, ["Kestrel"]) == (True, True)

        _unindex(store, item_id)
        assert searchability.keyword_reach(store, item_id, ["Kestrel"]) == (False, True)

        store.db.execute("DROP TABLE items_fts")
        store.db.commit()
        assert searchability.keyword_reach(store, item_id, ["Kestrel"]) == (
            False,
            False,
        )

    def test_every_reason_carries_a_detail_and_a_remedy(self):
        """A typed reason nobody can act on is a status code with extra syllables."""
        reasons = set(searchability.UNSEARCHABLE_REASONS)
        assert set(searchability.REASON_DETAIL) == reasons
        assert set(searchability.REASON_REMEDY) == reasons
        assert all(text.strip() for text in searchability.REASON_DETAIL.values())
        assert all(text.strip() for text in searchability.REASON_REMEDY.values())
