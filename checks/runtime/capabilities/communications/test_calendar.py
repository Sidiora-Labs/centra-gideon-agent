import asyncio
import json
import os
from datetime import datetime, timedelta, timezone

import pytest
from aiohttp import ClientSession, web

from gideon.interfaces.dashboard.handlers.capabilities_communications import register
from gideon.workspace.capabilities.communications import (
    PeopleError,
    PeopleStore,
    calendar,
)
from gideon.workspace.capabilities.communications.tools import create_provider


def source(store, kind="ics", **extra):
    return calendar.save_source(
        store, {"name": "Personal calendar", "kind": kind, **extra}
    )


def event(
    uid="meeting-one",
    start="DTSTART:20260925T090000Z",
    end="DTEND:20260925T100000Z",
    title="Team meeting",
    extra="",
):
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\n{start}\r\n{end}\r\nSUMMARY:{title}\r\nLOCATION:Office\\, floor 2\r\n{extra}END:VEVENT\r\n"


def ics(*events):
    return (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Calendar test export//EN\r\n"
        + "".join(events or [event()])
        + "END:VCALENDAR\r\n"
    )


def upload(store, row, content=None):
    return calendar.upload(
        store, row["id"], {"content": content or ics(), "revision": row["revision"]}
    )


def test_calendar_source_restart_revisions_and_configuration_invalidation(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    assert row["revision"] == 1
    assert row["timezone"] == "UTC"
    assert row["sync"]["coverage"] == "unknown"
    assert calendar.sources(PeopleStore(tmp_path)) == [row]
    upload(store, row)
    assert len(calendar.daily(store, "2026-09-25")["events"]) == 1
    data = {key: row[key] for key in calendar.SOURCE_FIELDS}
    updated = calendar.save_source(
        store, {**data, "name": "Renamed", "revision": 1}, row["id"]
    )
    assert updated["revision"] == 2
    assert updated["sync"]["state"] == "not_synced"
    assert calendar.daily(store, "2026-09-25")["events"] == []
    with pytest.raises(PeopleError) as error:
        calendar.save_source(store, {**data, "revision": 1}, row["id"])
    assert error.value.status == 409
    assert calendar.source(store, row["id"]) == updated
    with pytest.raises(PeopleError):
        calendar.save_source(
            store,
            {**data, "kind": "google", "credential_ref": "TOKEN", "revision": 2},
            row["id"],
        )


@pytest.mark.parametrize(
    "change",
    [
        {"name": ""},
        {"kind": "unsupported"},
        {"timezone": "Invalid/Zone"},
        {"home": "/outside"},
        {"kind": "google"},
        {"kind": "outlook", "credential_ref": "raw token"},
    ],
)
def test_source_validation(tmp_path, change):
    store = PeopleStore(tmp_path)
    with pytest.raises(PeopleError):
        calendar.save_source(store, {"name": "Calendar", "kind": "ics", **change})
    assert calendar.sources(store) == []


def test_actual_ics_import_provenance_and_replay(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    text = ics(event(title="A folded\r\n  meeting"))
    receipt = upload(store, row, text)
    assert receipt["coverage"] == "available_snapshot"
    assert receipt["scope"] == "uploaded_ics"
    assert receipt["events"] == 1
    assert receipt["source_revision"] == 1
    assert len(receipt["source_digest"]) == 64
    assert upload(PeopleStore(tmp_path), row, text) == receipt
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calendar_imports").fetchone()[0] == 1
        assert db.execute("SELECT raw FROM calendar_imports").fetchone()[0] == text
    review = calendar.daily(store, "2026-09-25")
    assert review["coverage"] == "available_snapshot"
    item = review["events"][0]
    assert item["title"] == "A folded meeting"
    assert item["location"] == "Office, floor 2"
    assert item["source_kind"] == "ics"
    assert item["uid"] == "meeting-one"
    assert item["start"] == "2026-09-25T09:00:00+00:00"
    assert item["end"] == "2026-09-25T10:00:00+00:00"
    assert item["all_day"] is False
    assert calendar.daily(PeopleStore(tmp_path), "2026-09-25") == review


def test_replacement_and_replay_do_not_resurrect_old_snapshot(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    old = ics(event())
    receipt = upload(store, row, old)
    new = ics(event("replacement", title="Replacement event"))
    upload(store, row, new)
    assert [
        event["uid"] for event in calendar.daily(store, "2026-09-25")["events"]
    ] == ["replacement"]
    assert upload(store, row, old) == receipt
    assert [
        event["uid"] for event in calendar.daily(store, "2026-09-25")["events"]
    ] == ["replacement"]
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM calendar_imports").fetchone()[0] == 2


def test_all_day_exclusive_end_and_timezone_independent_date(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store, timezone="Europe/Berlin")
    upload(
        store,
        row,
        ics(
            event(
                "holiday",
                "DTSTART;VALUE=DATE:20260925",
                "DTEND;VALUE=DATE:20260927",
                "Two day holiday",
            )
        ),
    )
    assert calendar.daily(store, "2026-09-24")["events"] == []
    for day in ["2026-09-25", "2026-09-26"]:
        for tz in ["UTC", "America/Los_Angeles", "Asia/Tokyo"]:
            review = calendar.daily(store, day, tz)
            assert review["events"][0]["all_day"] is True
            assert review["events"][0]["start"] == "2026-09-25"
            assert review["events"][0]["end"] == "2026-09-27"
    assert calendar.daily(store, "2026-09-27")["events"] == []


def test_timed_midnight_overlap_and_local_day_filter(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    upload(
        store,
        row,
        ics(
            event(
                "late",
                "DTSTART:20260925T233000Z",
                "DTEND:20260926T003000Z",
                "Late meeting",
            )
        ),
    )
    assert len(calendar.daily(store, "2026-09-25", "UTC")["events"]) == 1
    assert len(calendar.daily(store, "2026-09-26", "UTC")["events"]) == 1
    assert calendar.daily(store, "2026-09-25", "Europe/Berlin")["events"] == []
    assert len(calendar.daily(store, "2026-09-26", "Europe/Berlin")["events"]) == 1
    assert len(calendar.daily(store, "2026-09-25", "America/New_York")["events"]) == 1
    assert calendar.daily(store, "2026-09-26", "America/New_York")["events"] == []


def test_iana_floating_times_and_dst_gap_fold_rejection(tmp_path):
    events, warnings = calendar.parse_ics(
        ics(
            event(
                start="DTSTART;TZID=Europe/Berlin:20260925T090000",
                end="DTEND;TZID=Europe/Berlin:20260925T100000",
            )
        ),
        "UTC",
    )
    assert warnings == []
    assert events[0]["start"] == "2026-09-25T07:00:00+00:00"
    events, _ = calendar.parse_ics(
        ics(event(start="DTSTART:20260925T090000", end="DTEND:20260925T100000")),
        "America/New_York",
    )
    assert events[0]["start"] == "2026-09-25T13:00:00+00:00"
    for timestamp in ["20260329T023000", "20261025T023000"]:
        with pytest.raises(PeopleError, match="Ambiguous or nonexistent"):
            calendar.parse_ics(
                ics(event(start="DTSTART;TZID=Europe/Berlin:" + timestamp)), "UTC"
            )


def test_recurrence_is_explicitly_partial_and_cancelled_excluded(tmp_path):
    store = PeopleStore(tmp_path)
    row = source(store)
    data = ics(
        event("recurring", extra="RRULE:FREQ=DAILY;COUNT=3\r\n"),
        event("cancelled", title="Cancelled appointment", extra="STATUS:CANCELLED\r\n"),
    )
    receipt = upload(store, row, data)
    assert receipt["coverage"] == "partial"
    assert receipt["warnings"] == [{"uid": "recurring", "unsupported": ["RRULE"]}]
    review = calendar.daily(store, "2026-09-25")
    assert review["coverage"] == "partial"
    assert len(review["events"]) == 1
    assert review["events"][0]["recurrence_unexpanded"] is True
    assert review["events"][0]["uid"] == "recurring"
    assert calendar.daily(store, "2026-09-26")["events"] == []


@pytest.mark.parametrize(
    "text",
    [
        "not a calendar",
        "BEGIN:VCALENDAR\nEND:VCALENDAR",
        ics(event() + event()),
        ics(event(start="DTSTART:invalid")),
        ics(event(end="DTEND:20260924T090000Z")),
        ics(event(start="DTSTART;VALUE=DATE:20260925")),
        ics(event().replace("END:VEVENT", "")),
    ],
)
def test_malformed_ics_never_replaces_previous_source(tmp_path, text):
    store = PeopleStore(tmp_path)
    row = source(store)
    upload(store, row)
    before = calendar.daily(store, "2026-09-25")
    with pytest.raises(PeopleError):
        upload(store, row, text)
    assert calendar.daily(store, "2026-09-25") == before


def test_unknown_empty_timezone_and_stale_coverage_are_distinct(tmp_path):
    store = PeopleStore(tmp_path)
    assert calendar.daily(store, "2026-09-25")["coverage"] == "unknown"
    with pytest.raises(PeopleError):
        calendar.daily(store, "2026-09-25", "Not/AZone")
    for invalid in ["20260925", "2026-02-30", "", None]:
        with pytest.raises(PeopleError):
            calendar.daily(store, invalid)
    row = source(store)
    upload(store, row)
    empty = calendar.daily(store, "2026-09-24")
    assert empty["events"] == []
    assert empty["coverage"] == "available_snapshot"
    old = calendar.source(store, row["id"])
    old["sync"]["captured_at"] = (
        datetime.now(timezone.utc) - timedelta(days=2)
    ).isoformat()
    calendar.persist(store, old, calendar.parse_ics(ics(), "UTC")[0], old["sync"])
    stale = calendar.daily(store, "2026-09-25")
    assert len(stale["events"]) == 1
    assert stale["coverage"] == "partial"
    assert stale["sources"][0]["review_coverage"] == "unknown"


def test_google_and_outlook_actual_event_shapes_normalize():
    google = {
        "id": "google-instance",
        "iCalUID": "stable-uid",
        "summary": "Google event",
        "start": {"dateTime": "2026-09-25T09:00:00+02:00"},
        "end": {"dateTime": "2026-09-25T10:00:00+02:00"},
        "location": "Office",
    }
    result = calendar.provider_event(google, "google")
    assert result["start"] == "2026-09-25T07:00:00+00:00"
    assert result["uid"] == "stable-uid"
    assert result["title"] == "Google event"
    outlook = {
        "id": "outlook-instance",
        "iCalUId": "outlook-uid",
        "subject": "Outlook event",
        "start": {"dateTime": "2026-09-25T09:00:00.0000000", "timeZone": "UTC"},
        "end": {"dateTime": "2026-09-25T10:00:00.0000000", "timeZone": "UTC"},
        "location": {"displayName": "Remote"},
    }
    result = calendar.provider_event(outlook, "outlook")
    assert result["start"] == "2026-09-25T09:00:00+00:00"
    assert result["location"] == "Remote"
    assert result["uid"] == "outlook-uid"
    assert result["recurrence_unexpanded"] is False
    cancelled = calendar.provider_event({**outlook, "isCancelled": True}, "outlook")
    assert cancelled["status"] == "cancelled"
    all_day = calendar.provider_event(
        {**google, "start": {"date": "2026-09-25"}, "end": {"date": "2026-09-26"}},
        "google",
    )
    assert all_day["all_day"] is True
    assert all_day["start"] == "2026-09-25"
    with pytest.raises(PeopleError):
        calendar.provider_event({**outlook, "location": "bad shape"}, "outlook")


def test_remote_missing_connection_is_durable_failure_without_erasing_cached_events(
    tmp_path,
):
    store = PeopleStore(tmp_path)
    row = source(store, "google", credential_ref="ABSENT_CALENDAR_TEST_479E")
    parsed, _ = calendar.parse_ics(ics(), "UTC")
    state = {
        "state": "synced",
        "coverage": "available_snapshot",
        "scope": "provider_window",
        "start": "2026-09-25T00:00:00+00:00",
        "end": "2026-09-26T00:00:00+00:00",
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    calendar.persist(store, row, parsed, state)
    assert calendar.daily(store, "2026-09-25")["coverage"] == "available_snapshot"
    assert (
        calendar.daily(store, "2026-09-26")["sources"][0]["review_coverage"]
        == "unknown"
    )
    with pytest.raises(PeopleError) as error:
        asyncio.run(
            calendar.sync_remote(
                store, row["id"], {"start": state["start"], "end": state["end"]}
            )
        )
    assert error.value.status == 503
    current = calendar.source(store, row["id"])
    assert current["sync"]["state"] == "failed"
    assert current["sync"]["coverage"] == "unknown"
    assert len(calendar.daily(store, "2026-09-25")["events"]) == 1
    assert calendar.daily(store, "2026-09-25")["coverage"] == "partial"


def test_source_scope_revision_and_duplicate_event_rollback(tmp_path):
    first = PeopleStore(tmp_path / "first")
    second = PeopleStore(tmp_path / "second")
    row = source(first)
    upload(first, row)
    assert calendar.sources(second) == []
    assert calendar.daily(second, "2026-09-25")["events"] == []
    with pytest.raises(PeopleError) as error:
        upload(second, row)
    assert error.value.status == 404
    with pytest.raises(PeopleError):
        calendar.upload(first, row["id"], {"content": ics(), "revision": 0})
    parsed, _ = calendar.parse_ics(ics(), "UTC")
    with pytest.raises(PeopleError):
        calendar.persist(first, row, parsed * 2, {"coverage": "available_snapshot"})
    assert len(calendar.daily(first, "2026-09-25")["events"]) == 1


def test_native_calendar_tools_actual_source_and_review(tmp_path):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(tmp_path)

    async def scenario():
        provider = create_provider()
        result = await provider.invoke(
            "people_calendar_create",
            {"source": {"name": "Native calendar", "kind": "ics"}},
        )
        assert result.success, result.error
        row = json.loads(result.output)["source"]
        result = await provider.invoke(
            "people_calendar_upload",
            {"source_id": row["id"], "content": ics(), "revision": 1},
        )
        assert json.loads(result.output)["sync"]["events"] == 1
        result = await provider.invoke(
            "people_calendar_daily", {"date": "2026-09-25", "timezone": "UTC"}
        )
        assert json.loads(result.output)["events"][0]["title"] == "Team meeting"
        result = await provider.invoke("people_calendar_sources", {})
        assert len(json.loads(result.output)["sources"]) == 1
        result = await provider.invoke(
            "people_calendar_update",
            {
                "source_id": row["id"],
                "source": {"name": "Updated", "kind": "ics", "revision": 1},
            },
        )
        assert json.loads(result.output)["source"]["revision"] == 2
        result = await provider.invoke("people_calendar_daily", {"date": "2026-09-25"})
        assert json.loads(result.output)["events"] == []
        definitions = {tool.name: tool for tool in await provider.list_tools()}
        assert definitions["people_calendar_daily"].requires_approval is False
        assert definitions["people_calendar_sync"].requires_approval is True

    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous


def test_actual_http_calendar_import_review_and_conflicts(tmp_path):
    previous = os.environ.get("GIDEON_HOME")
    os.environ["GIDEON_HOME"] = str(tmp_path)

    async def scenario():
        app = web.Application()
        register(app)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        base = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/api/capabilities/communications/calendar"
        try:
            async with ClientSession() as client:
                async with client.post(
                    base + "/sources", json={"name": "HTTP calendar", "kind": "ics"}
                ) as response:
                    assert response.status == 201
                    row = (await response.json())["source"]
                path = base + "/sources/" + row["id"]
                async with client.post(
                    path + "/upload", json={"content": ics(), "revision": 1}
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["sync"]["events"] == 1
                async with client.get(
                    base + "/daily", params={"date": "2026-09-25", "timezone": "UTC"}
                ) as response:
                    assert (await response.json())["events"][0]["uid"] == "meeting-one"
                async with client.get(
                    base + "/daily",
                    params={"date": "2026-09-25", "timezone": "Invalid/Zone"},
                ) as response:
                    assert response.status == 400
                async with client.put(
                    path, json={"name": "Edited", "kind": "ics", "revision": 1}
                ) as response:
                    assert (await response.json())["source"]["revision"] == 2
                async with client.post(
                    path + "/upload", json={"content": ics(), "revision": 1}
                ) as response:
                    assert response.status == 409
                async with client.get(base + "/sources") as response:
                    assert len((await response.json())["sources"]) == 1
        finally:
            await runner.cleanup()

    try:
        asyncio.run(scenario())
    finally:
        if previous is None:
            os.environ.pop("GIDEON_HOME", None)
        else:
            os.environ["GIDEON_HOME"] = previous
