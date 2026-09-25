"""Real SQLite stores and actual aiohttp requests for anniversary resurfacing."""

import asyncio
import json
from datetime import datetime
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.cognition.memory import MemoryJournal
from gideon.cognition.memory_service import MemoryService
from gideon.cognition.vector_memory import SemanticArchive
from gideon.core.config.loader import AppConfig
from gideon.engine.session import ConversationDirectory
from gideon.interfaces.dashboard.handlers.capabilities_knowledge import register
from gideon.interfaces.dashboard.state import ConsoleState
from gideon.workspace.capabilities.knowledge.anniversaries import (
    anniversaries,
    source_record,
)


@pytest.fixture
def store(tmp_path):
    result = KnowledgeStore(str(tmp_path / "knowledge.db"))
    yield result
    result.close()


def create(
    store, title, stamp, kind="note", metadata=None, content="A remembered day."
):
    extra = {"created_at": stamp}
    if metadata is not None:
        extra["file_metadata"] = metadata
    identity = store.create_typed_item(
        item_type=kind, title=title, content=content, extra=extra
    )
    assert identity is not None
    store.db.execute("UPDATE items SET created_at = ? WHERE id = ?", (stamp, identity))
    store.db.commit()
    return identity


def test_prior_year_types_and_source_links(store):
    note = create(store, "Walk", "2025-09-25T09:30:00+02:00")
    journal = create(store, "Journal", "2024-09-25", "journal")
    fleeting = create(store, "Idea", "2023-09-25", "fleeting")
    create(store, "Today", "2026-09-25")
    create(store, "Future", "2027-09-25")
    create(store, "Other date", "2025-09-24")
    create(store, "Bookmark", "2025-09-25", "bookmark")
    result = anniversaries(store, date="2026-09-25", timezone="Europe/Berlin")
    assert [row["source_id"] for row in result["items"]] == [note, journal, fleeting]
    assert [row["years_ago"] for row in result["items"]] == [1, 2, 3]
    assert [row["source_type"] for row in result["items"]] == [
        "note",
        "journal",
        "fleeting",
    ]
    assert result["items"][0]["source_link"] == f"#/knowledge/item/{note}"
    assert result["items"][0]["original_at"] == "2025-09-25T09:30:00+02:00"
    assert result["total"] == 3
    assert result["next_offset"] is None


def test_source_date_has_priority_over_ingestion(store):
    old = create(
        store,
        "Imported",
        "2026-09-25",
        metadata={"original_at": "2022-09-25T12:00:00Z"},
    )
    alternate = create(
        store,
        "Imported source",
        "2026-01-01",
        metadata={"source_created_at": "2021-09-25"},
    )
    create(
        store,
        "Imported other day",
        "2025-09-25",
        metadata={"original_at": "2020-01-01"},
    )
    result = anniversaries(store, date="2026-09-25")
    assert [row["source_id"] for row in result["items"]] == [old, alternate]
    assert [row["years_ago"] for row in result["items"]] == [4, 5]
    assert result["items"][0]["original_at"] == "2022-09-25T12:00:00Z"
    assert store.get_item(old)["created_at"] == "2026-09-25"


def test_offsets_cross_local_calendar_midnight(store):
    identity = create(store, "Late UTC", "2025-09-24T23:30:00Z")
    berlin = anniversaries(store, date="2026-09-25", timezone="Europe/Berlin")
    utc = anniversaries(store, date="2026-09-25", timezone="UTC")
    previous = anniversaries(store, date="2026-09-24", timezone="America/Los_Angeles")
    assert [row["source_id"] for row in berlin["items"]] == [identity]
    assert utc["total"] == 0
    assert previous["items"][0]["source_id"] == identity
    assert berlin["timezone"] == "Europe/Berlin"


def test_date_only_and_legacy_wall_clock_are_explicit(store):
    date_only = create(store, "Date", "2024-09-25", "journal")
    legacy = create(store, "Legacy", "2025-09-25T00:01:00")
    result = anniversaries(store, date="2026-09-25", timezone="Pacific/Honolulu")
    assert {row["source_id"] for row in result["items"]} == {date_only, legacy}
    assert result["naive_timestamp_policy"] == "local_wall_clock"
    assert result["items"][0]["original_at"] == "2025-09-25T00:01:00"


def test_exact_leap_day_policy(store):
    leap = create(store, "Leap", "2020-02-29")
    create(store, "Non leap", "2021-02-28")
    leap_result = anniversaries(store, date="2024-02-29")
    february = anniversaries(store, date="2025-02-28")
    march = anniversaries(store, date="2025-03-01")
    assert leap_result["leap_day_policy"] == "exact"
    assert leap_result["items"][0]["source_id"] == leap
    assert leap_result["items"][0]["years_ago"] == 4
    assert all(row["source_id"] != leap for row in february["items"])
    assert march["total"] == 0


def test_invalid_original_date_is_not_replaced_by_import_date(store):
    create(
        store, "Corrupt source", "2025-09-25", metadata={"original_at": "not-a-date"}
    )
    create(store, "Missing source", "2025-09-25", metadata={"original_at": None})
    valid = create(store, "Valid", "2024-09-25")
    result = anniversaries(store, date="2026-09-25")
    assert result["skipped_invalid_dates"] == 2
    assert result["total"] == 1
    assert result["items"][0]["source_id"] == valid


def test_archived_deleted_and_inactive_sources_are_not_exposed(store):
    archived = create(store, "Archived", "2025-09-25")
    inactive = create(store, "Inactive", "2025-09-25")
    removed = create(store, "Removed", "2025-09-25")
    store.update_item(archived, is_archived=1)
    store.update_item(inactive, status="superseded")
    store.delete_item(removed)
    result = anniversaries(store, date="2026-09-25")
    assert result["total"] == 0
    assert source_record(store, None, "note", archived) is None
    assert source_record(store, None, "note", inactive) is None
    assert source_record(store, None, "note", removed) is None


def test_pagination_is_stable_and_complete(store):
    identities = [create(store, f"Note {i}", "2025-09-25") for i in range(7)]
    first = anniversaries(store, date="2026-09-25", limit=3)
    second = anniversaries(
        store, date="2026-09-25", limit=3, offset=first["next_offset"]
    )
    last = anniversaries(
        store, date="2026-09-25", limit=3, offset=second["next_offset"]
    )
    rows = first["items"] + second["items"] + last["items"]
    assert [row["source_id"] for row in rows] == sorted(identities)
    assert first["next_offset"] == 3
    assert second["next_offset"] == 6
    assert last["next_offset"] is None
    assert first["total"] == second["total"] == last["total"] == 7
    assert anniversaries(store, date="2026-09-25", offset=99)["items"] == []


def test_source_read_keeps_full_unicode_content(store):
    content = "A café by the sea 🌊\n" + "Thoughts and details. " * 60
    identity = create(store, "旅", "2025-09-25", content=content)
    result = anniversaries(store, date="2026-09-25")
    detail = source_record(store, None, "note", identity)
    assert result["items"][0]["excerpt"] == " ".join(content.split())[:240]
    assert "content" not in result["items"][0]
    assert detail["content"] == content
    assert detail["title"] == "旅"
    assert source_record(store, None, "journal", identity) is None


def test_reopen_reads_authoritative_edits(tmp_path):
    path = str(tmp_path / "persistent.db")
    first = KnowledgeStore(path)
    identity = create(first, "Original", "2025-09-25")
    first.close()
    second = KnowledgeStore(path)
    second.update_item(identity, title="Revised", content="Revised content")
    result = anniversaries(second, date="2026-09-25")
    assert result["items"][0]["title"] == "Revised"
    assert result["items"][0]["excerpt"] == "Revised content"
    assert result["items"][0]["source_id"] == identity
    assert result["items"][0]["original_at"] == "2025-09-25"
    second.close()


@pytest.mark.parametrize(
    "parameters",
    [
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"limit": 1.5},
        {"offset": -1},
        {"offset": 1000001},
        {"offset": False},
        {"timezone": "Missing/Zone"},
        {"timezone": "../../etc/passwd"},
        {"date": "2025-02-29"},
        {"date": "20260925"},
        {"date": ""},
    ],
)
def test_bounded_parameter_validation(store, parameters):
    with pytest.raises(ValueError):
        anniversaries(store, **parameters)


def test_default_date_uses_requested_timezone(store):
    zone = "Pacific/Kiritimati"
    before = datetime.now(ZoneInfo(zone)).date().isoformat()
    result = anniversaries(store, timezone=zone)
    after = datetime.now(ZoneInfo(zone)).date().isoformat()
    assert result["date"] in {before, after}
    assert result["sources"] == {"knowledge": "available", "memory": "unavailable"}
    assert result["items"] == []


def memory_store(tmp_path):
    archive = SemanticArchive(db_path=tmp_path / "memory.db")
    archive.init()
    assert archive.write_episodic(
        "A remembered expedition to the northern mountains, with friends."
    )
    row = archive.db.execute("SELECT id FROM episodic_memories").fetchone()
    archive.db.execute(
        "UPDATE episodic_memories SET created_at = ? WHERE id = ?",
        ("2025-09-25T12:00:00Z", row["id"]),
    )
    archive.db.commit()
    return archive, row["id"]


def test_real_memory_service_and_deleted_memory(store, tmp_path):
    archive, identity = memory_store(tmp_path)
    service = MemoryService.over_vector_store(archive)
    result = anniversaries(store, service, date="2026-09-25")
    assert result["sources"]["memory"] == "available"
    assert result["items"][0]["source_type"] == "memory"
    assert result["items"][0]["source_id"] == identity
    assert result["items"][0]["source_link"].endswith(quote(identity))
    assert source_record(store, service, "memory", identity)["content"].startswith(
        "A remembered expedition"
    )
    assert archive.delete_episodic(identity)
    assert anniversaries(store, service, date="2026-09-25")["total"] == 0
    assert source_record(store, service, "memory", identity) is None
    archive.close()


def state_for(store):
    state = ConsoleState(ConversationDirectory(AppConfig()), start_time=0)
    state._knowledge_store = store
    return state


def test_real_http_list_detail_validation_and_isolation(tmp_path):
    async def journey():
        left = KnowledgeStore(str(tmp_path / "left.db"))
        right = KnowledgeStore(str(tmp_path / "right.db"))
        identity = create(
            left, "Private note", "2025-09-25", content="The actual original text"
        )
        left_app = web.Application()
        right_app = web.Application()
        left_app["state"] = state_for(left)
        right_app["state"] = state_for(right)
        register(left_app)
        register(right_app)
        async with (
            TestClient(TestServer(left_app)) as a,
            TestClient(TestServer(right_app)) as b,
        ):
            response = await a.get(
                "/api/capabilities/knowledge/anniversaries?date=2026-09-25"
            )
            assert response.status == 200
            payload = await response.json()
            assert payload["items"][0]["source_id"] == identity
            detail = await a.get(f"/api/capabilities/knowledge/sources/note/{identity}")
            assert detail.status == 200
            assert (await detail.json())["content"] == "The actual original text"
            isolated = await b.get(
                "/api/capabilities/knowledge/anniversaries?date=2026-09-25"
            )
            assert (await isolated.json())["total"] == 0
            unavailable = await b.get(
                f"/api/capabilities/knowledge/sources/note/{identity}"
            )
            assert unavailable.status == 404
            for query in (
                "limit=0",
                "limit=101",
                "offset=-1",
                "offset=abc",
                "date=2025-02-29",
                "timezone=Unknown/Zone",
                "limit=1&limit=2",
            ):
                invalid = await a.get(
                    f"/api/capabilities/knowledge/anniversaries?{query}"
                )
                assert invalid.status == 400, query
                assert (await invalid.json())["error"]
            for selector in (
                "home",
                "account",
                "runtime",
                "provider",
                "model",
                "user",
                "tenant",
            ):
                refused = await a.get(
                    f"/api/capabilities/knowledge/anniversaries?{selector}=other"
                )
                assert refused.status == 400, selector
                source_refused = await a.get(
                    f"/api/capabilities/knowledge/sources/note/{identity}?{selector}=other"
                )
                assert source_refused.status == 400
            wrong_type = await a.get(
                f"/api/capabilities/knowledge/sources/memory/{identity}"
            )
            assert wrong_type.status == 404
            read_only = await a.post(
                "/api/capabilities/knowledge/anniversaries", json={"title": "New"}
            )
            assert read_only.status == 405
            assert left.db.execute("SELECT count(*) FROM items").fetchone()[0] == 1
        left.close()
        right.close()

    asyncio.run(journey())


def test_real_http_memory_exact_source(tmp_path):
    async def journey():
        store = KnowledgeStore(str(tmp_path / "knowledge.db"))
        archive, identity = memory_store(tmp_path)
        journal = MemoryJournal(workspace=tmp_path / "workspace")
        journal.vector_store = archive
        state = state_for(store)
        state._standalone_memory = journal
        app = web.Application()
        app["state"] = state
        register(app)
        async with TestClient(TestServer(app)) as client:
            response = await client.get(
                "/api/capabilities/knowledge/anniversaries?date=2026-09-25"
            )
            data = await response.json()
            assert response.status == 200
            assert data["sources"]["memory"] == "available"
            assert data["items"][0]["source_id"] == identity
            detail = await client.get(
                f"/api/capabilities/knowledge/sources/memory/{identity}"
            )
            content = await detail.json()
            assert detail.status == 200
            assert content["source_type"] == "memory"
            assert content["original_at"] == "2025-09-25T12:00:00Z"
            assert "mountains" in content["content"]
            assert archive.embed_fn is None
        store.close()
        archive.close()

    asyncio.run(journey())
