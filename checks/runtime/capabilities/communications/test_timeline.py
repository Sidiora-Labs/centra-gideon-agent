import asyncio
import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from aiohttp import ClientSession, web

from gideon.core.config.loader import AgentProfile, AppConfig
from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    calendar,
    evidence,
    lifecycle,
    social,
    timeline,
)
from gideon.workspace.capabilities.communications.tools import create_provider


@contextmanager
def runtime_home(path):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(path)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous


def person(store, name="Alice"):
    return store.save({"name": name})


def point(
    store,
    row,
    identity="point1",
    when="2026-09-25T09:00:00Z",
    summary="Coffee discussion",
):
    return store.record(
        row["id"],
        {
            "source": "manual",
            "external_id": identity,
            "occurred_at": when,
            "direction": "mutual",
            "summary": summary,
        },
    )[0]


def read(store, **extra):
    return timeline.read(store, {"date": "2026-09-25", "timezone": "UTC", **extra})


def test_empty_projection_does_not_create_competing_activity_tables(tmp_path):
    store = PeopleStore(tmp_path)
    result = read(store)
    assert result["events"] == []
    assert result["coverage"] == "available_local_records"
    assert result["next_cursor"] is None
    assert result["matched_records"] == 0
    assert result["available_sources"] == ["touchpoints"]
    assert any("not a complete record" in limit for limit in result["limits"])
    with store.connect() as db:
        tables = {
            row[0]
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert tables == {"people", "touchpoints"}


def test_canonical_contact_and_message_projection_without_duplicate_mirror_rows(
    tmp_path,
):
    store = PeopleStore(tmp_path)
    row = person(store)
    manual = point(store, row)
    batch = {
        "source": "imap",
        "source_account_id": "personal",
        "captured_at": "2026-09-25T12:00:00Z",
        "coverage_start": "2026-09-25T00:00:00Z",
        "coverage_end": "2026-09-25T12:00:00Z",
        "incoming_complete": False,
        "outgoing_complete": False,
        "messages": [
            {
                "person_id": row["id"],
                "thread_id": "thread1",
                "external_id": "message1",
                "occurred_at": "2026-09-25T10:00:00Z",
                "direction": "inbound",
                "summary": "Mail reply",
            }
        ],
    }
    evidence.ingest(store, batch)
    evidence.ingest(store, batch)
    result = read(store)
    assert len(result["events"]) == 2
    assert [event["kind"] for event in result["events"]] == ["message", "touchpoint"]
    assert result["events"][1]["id"] == "touchpoint:" + manual["id"]
    assert result["events"][0]["summary"] == "Mail reply"
    assert result["events"][0]["qualification"] == "recorded_message_not_complete_inbox"
    assert result["events"][0]["person_id"] == row["id"]
    assert "person=" + row["id"] in result["events"][0]["href"]
    assert result["events"][0]["source"] == "imap"
    assert read(PeopleStore(tmp_path)) == result
    assert len(read(store, kind="message")["events"]) == 1
    assert len(read(store, person_id=row["id"])["events"]) == 2


def test_local_day_boundaries_and_dst_intervals(tmp_path):
    store = PeopleStore(tmp_path)
    row = person(store)
    point(store, row, "before", "2026-09-24T21:59:59Z")
    point(store, row, "start", "2026-09-24T22:00:00Z", "Local midnight")
    point(store, row, "end", "2026-09-25T21:59:59Z", "Before next midnight")
    point(store, row, "after", "2026-09-25T22:00:00Z")
    result = read(store, timezone="Europe/Berlin")
    assert [event["summary"] for event in result["events"]] == [
        "Before next midnight",
        "Local midnight",
    ]
    point(store, row, "dst-before", "2026-03-28T23:00:00Z", "DST start day")
    point(store, row, "dst-last", "2026-03-29T21:59:59Z", "DST day end")
    point(store, row, "dst-after", "2026-03-29T22:00:00Z")
    result = read(store, date="2026-03-29", timezone="Europe/Berlin")
    assert len(result["events"]) == 2
    assert result["events"][0]["summary"] == "DST day end"


def test_person_filters_do_not_infer_calendar_attendance(tmp_path):
    store = PeopleStore(tmp_path)
    alice = person(store)
    bob = person(store, "Bob")
    point(store, alice, "alice")
    point(store, bob, "bob")
    source = calendar.save_source(store, {"name": "Personal", "kind": "ics"})
    content = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:meeting\r\nDTSTART:20260925T080000Z\r\nDTEND:20260925T090000Z\r\nSUMMARY:Scheduled meeting\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    calendar.upload(store, source["id"], {"content": content, "revision": 1})
    events = read(store)["events"]
    assert len(events) == 3
    scheduled = next(event for event in events if event["kind"] == "calendar")
    assert scheduled["qualification"] == "scheduled_not_attendance"
    assert scheduled["person_id"] is None
    assert "calendar_source=" + source["id"] in scheduled["href"]
    assert len(read(store, person_id=alice["id"])["events"]) == 1
    assert read(store, kind="calendar", person_id=alice["id"])["events"] == []
    assert len(read(store, kind="calendar")["events"]) == 1


def test_all_day_multiday_overlap_and_cancelled_calendar_events(tmp_path):
    store = PeopleStore(tmp_path)
    source = calendar.save_source(store, {"name": "Calendar", "kind": "ics"})
    content = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:trip\r\nDTSTART;VALUE=DATE:20260924\r\nDTEND;VALUE=DATE:20260926\r\nSUMMARY:Scheduled trip\r\nEND:VEVENT\r\nBEGIN:VEVENT\r\nUID:cancelled\r\nDTSTART:20260925T090000Z\r\nDTEND:20260925T100000Z\r\nSUMMARY:Cancelled\r\nSTATUS:CANCELLED\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    calendar.upload(store, source["id"], {"content": content, "revision": 1})
    result = read(store, timezone="Europe/Berlin")
    assert len(result["events"]) == 1
    assert result["events"][0]["summary"] == "Scheduled trip"
    assert result["events"][0]["occurred_at"] == "2026-09-24T22:00:00+00:00"
    assert read(store, date="2026-09-26", timezone="Europe/Berlin")["events"] == []


def test_stable_seek_pagination_and_query_bound_cursor(tmp_path):
    store = PeopleStore(tmp_path)
    row = person(store)
    for index in range(6):
        point(store, row, str(index), summary="Point " + str(index))
    full = read(store)["events"]
    first = read(store, limit=2)
    assert len(first["events"]) == 2
    assert first["next_cursor"]
    second = read(store, limit=2, cursor=first["next_cursor"])
    third = read(store, limit=2, cursor=second["next_cursor"])
    assert third["next_cursor"] is None
    assert first["events"] + second["events"] + third["events"] == full
    with pytest.raises(PeopleError):
        read(store, limit=2, cursor=first["next_cursor"], timezone="Europe/Berlin")
    with pytest.raises(PeopleError):
        read(store, limit=2, cursor=first["next_cursor"], kind="touchpoint")
    with pytest.raises(PeopleError):
        read(store, limit=2, cursor=first["next_cursor"], person_id=row["id"])
    point(store, row, "newer", "2026-09-25T10:00:00Z", "New arrival")
    assert (
        read(store, limit=2, cursor=first["next_cursor"])["events"] == second["events"]
    )


def test_registry_and_assignment_history_project_only_safe_summary_fields(tmp_path):
    with runtime_home(tmp_path):
        cfg = AppConfig.load()
        cfg.agents["research"] = AgentProfile(provider="native")
        cfg.save()
        store = PeopleStore()
        row = person(store)
        account, _ = social.save(
            store,
            {
                "platform": "x",
                "handle": "alice",
                "request_key": "account",
                "person_id": row["id"],
                "credential_ref": "SECRET_REFERENCE_NOT_FOR_TIMELINE",
                "notes": "Private account note",
            },
        )
        assignment = lifecycle.create(
            store,
            {
                "account_id": account["id"],
                "account_revision": 1,
                "agent_id": "research",
                "reason": "Private operational reason",
                "request_key": "assignment",
            },
        )
        day = datetime.now(timezone.utc).date().isoformat()
        result = read(store, date=day)
        assert {event["kind"] for event in result["events"]} == {"social", "assignment"}
        encoded = json.dumps(result)
        assert "SECRET_REFERENCE_NOT_FOR_TIMELINE" not in encoded
        assert "Private account note" not in encoded
        assert "Private operational reason" not in encoded
        linked = read(store, date=day, person_id=row["id"])
        assert len(linked["events"]) == 1
        assert linked["events"][0]["summary"] == "created x @alice"
        assignment_event = next(
            event for event in result["events"] if event["kind"] == "assignment"
        )
        assert "platform_assignment=" + assignment["id"] in assignment_event["href"]


@pytest.mark.parametrize(
    "extra",
    [
        {"date": "bad"},
        {"date": "9999-12-31"},
        {"timezone": "Bad/Zone"},
        {"limit": 0},
        {"limit": 101},
        {"limit": True},
        {"kind": "imagined"},
        {"cursor": "not-base64"},
        {"home": "/another"},
        {"person_id": "missing"},
    ],
)
def test_timeline_invalid_queries_fail_explicitly(tmp_path, extra):
    with pytest.raises(PeopleError):
        read(PeopleStore(tmp_path), **extra)


def test_native_timeline_reads_current_home_without_mutating_sources(tmp_path):
    with runtime_home(tmp_path / "one"):
        store = PeopleStore()
        row = person(store)
        point(store, row)
        with store.connect() as db:
            before = list(db.iterdump())

        async def scenario():
            provider = create_provider()
            result = await provider.invoke(
                "people_activity_timeline", {"date": "2026-09-25", "timezone": "UTC"}
            )
            assert result.success, result.error
            feed = json.loads(result.output)
            assert feed["events"][0]["summary"] == "Coffee discussion"
            definitions = {tool.name: tool for tool in await provider.list_tools()}
            assert definitions["people_activity_timeline"].requires_approval is False

        asyncio.run(scenario())
        with store.connect() as db:
            assert list(db.iterdump()) == before
    with runtime_home(tmp_path / "two"):
        assert read(PeopleStore())["events"] == []


def test_scan_cap_reports_partial_instead_of_complete_history(tmp_path):
    store = PeopleStore(tmp_path)
    row = person(store)
    sample = point(store, row)
    with store.connect() as db, db:
        records = []
        for index in range(10000):
            identity = "bulk-" + str(index)
            body = {**sample, "id": identity, "external_id": identity}
            records.append((identity, row["id"], "manual", identity, json.dumps(body)))
        db.executemany("INSERT INTO touchpoints VALUES (?,?,?,?,?)", records)
    result = read(store, limit=100)
    assert result["coverage"] == "truncated_local_records"
    assert result["matched_records"] == 10000
    assert len(result["events"]) == 100
    assert result["next_cursor"]


def test_actual_http_timeline_person_query_and_invalid_limit(tmp_path):
    with runtime_home(tmp_path):
        store = PeopleStore()
        row = person(store)
        point(store, row)

        async def scenario():
            app = web.Application()
            register(app)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/activity-timeline"
            try:
                async with ClientSession() as client:
                    async with client.get(
                        base,
                        params={
                            "date": "2026-09-25",
                            "person_id": row["id"],
                            "limit": "1",
                        },
                    ) as response:
                        assert response.status == 200
                        result = await response.json()
                        assert result["events"][0]["person_id"] == row["id"]
                    async with client.get(
                        base, params={"date": "2026-09-25", "limit": "abc"}
                    ) as response:
                        assert response.status == 400
                    async with client.get(
                        base, params={"date": "9999-12-31"}
                    ) as response:
                        assert response.status == 400
                    async with client.get(
                        base, params={"date": "2026-09-25", "home": "/other"}
                    ) as response:
                        assert response.status == 400
            finally:
                await runner.cleanup()

        asyncio.run(scenario())
