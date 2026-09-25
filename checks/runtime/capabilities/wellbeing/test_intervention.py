import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_intervention import (
    register,
)
from gideon.workspace.capabilities.wellbeing.intervention import (
    InterventionStore,
    calendar_date,
)
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def plan(**changes):
    value = dict(
        request_id="plan-1",
        name="Evening walk",
        kind="activity",
        instructions="My planned walk",
        source="personal plan",
        timezone="Europe/Berlin",
        start_date="2026-09-20",
        end_date=None,
        weekdays=[0, 1, 2, 3, 4, 5, 6],
    )
    value.update(changes)
    return value


def record(**changes):
    value = dict(
        request_id="record-1",
        date="2026-09-24",
        status="completed",
        observed_at="2026-09-24T20:00:00+02:00",
        notes="logged observation",
    )
    value.update(changes)
    return value


def test_plan_persistence_history_and_immutable_schedule(tmp_path):
    store = InterventionStore(tmp_path)
    row = store.create_plan(plan())
    assert row["name"] == "Evening walk"
    assert row["kind"] == "activity"
    assert row["timezone"] == "Europe/Berlin"
    assert row["weekdays"] == list(range(7))
    assert row["end_date"] is None
    assert row["source"] == "personal plan"
    assert row["revision"] == 1
    assert row["archived"] is False
    assert store.create_plan(plan()) == row
    assert InterventionStore(tmp_path).get_plan(row["id"]) == row
    assert store.list_plans() == [row]
    updated = store.update_plan(
        row["id"],
        dict(
            request_id="edit",
            revision=1,
            name="My evening walk",
            instructions="Updated personal instructions",
        ),
    )
    assert updated["revision"] == 2
    assert updated["name"] == "My evening walk"
    assert updated["instructions"] == "Updated personal instructions"
    assert updated["source"] == row["source"]
    assert updated["created_at"] == row["created_at"]
    assert updated["weekdays"] == row["weekdays"]
    assert store.history_plan(row["id"]) == [row, updated]
    with pytest.raises(MeasurementError, match="changed"):
        store.update_plan(row["id"], dict(request_id="stale", revision=1, name="stale"))
    for key, value in [
        ("source", "changed"),
        ("timezone", "UTC"),
        ("start_date", "2026-09-22"),
        ("kind", "other"),
        ("weekdays", [1]),
    ]:
        with pytest.raises(MeasurementError, match="immutable"):
            store.update_plan(
                row["id"], dict(request_id=key, revision=2, **{key: value})
            )
    assert store.get_plan(row["id"]) == updated


def test_observation_corrections_retry_day_identity_and_provenance(tmp_path):
    store = InterventionStore(tmp_path)
    parent = store.create_plan(plan())
    row = store.record(parent["id"], record())
    assert row["plan_id"] == parent["id"]
    assert row["plan_revision"] == 1
    assert row["source"] == parent["source"]
    assert row["date"] == "2026-09-24"
    assert row["status"] == "completed"
    assert store.record(parent["id"], record()) == row
    assert store.list_records(parent["id"]) == [row]
    with pytest.raises(MeasurementError, match="already has a record"):
        store.record(parent["id"], record(request_id="duplicate"))
    correction = dict(
        request_id="correct",
        revision=1,
        status="skipped",
        notes="corrected observation",
    )
    updated = store.correct_record(row["id"], correction)
    assert updated["revision"] == 2
    assert updated["status"] == "skipped"
    assert updated["notes"] == "corrected observation"
    assert updated["observed_at"] == row["observed_at"]
    assert updated["plan_revision"] == 1
    assert updated["created_at"] == row["created_at"]
    assert store.correct_record(row["id"], correction) == updated
    assert InterventionStore(tmp_path).get_record(row["id"]) == updated
    assert store.history_record(row["id"]) == [row, updated]
    with pytest.raises(MeasurementError, match="changed"):
        store.correct_record(row["id"], dict(correction, request_id="stale"))
    with pytest.raises(MeasurementError, match="Request ID"):
        store.correct_record(row["id"], dict(correction, notes="different"))
    with pytest.raises(MeasurementError, match="immutable"):
        store.correct_record(
            row["id"], dict(request_id="date", revision=2, date="2026-09-25")
        )
    assert store.list_records(parent["id"]) == [updated]


def test_summary_preserves_unrecorded_skipped_and_excludes_future_observation(tmp_path):
    store = InterventionStore(tmp_path)
    parent = store.create_plan(plan())
    empty = store.summary(parent["id"], days=3, as_of="2026-09-25T12:00:00+02:00")
    assert empty["scheduled_days"] == 3
    assert empty["unrecorded_days"] == 3
    assert empty["completion_rate"] is None
    assert empty["recording_rate"] == 0
    assert all(day["status"] is None for day in empty["days"])
    completed = store.record(parent["id"], record())
    skipped = store.record(
        parent["id"], record(request_id="skip", date="2026-09-23", status="skipped")
    )
    future = store.record(
        parent["id"],
        record(
            request_id="future",
            date="2026-09-25",
            observed_at="2026-09-25T20:00:00+02:00",
        ),
    )
    result = store.summary(parent["id"], days=3, as_of="2026-09-25T12:00:00+02:00")
    assert result["timezone"] == "Europe/Berlin"
    assert result["completed_days"] == 1
    assert result["skipped_days"] == 1
    assert result["unrecorded_days"] == 1
    assert result["completion_rate"] == 0.5
    assert result["recording_rate"] == pytest.approx(2 / 3)
    assert result["days"][0]["record_id"] == skipped["id"]
    assert result["days"][1]["record_id"] == completed["id"]
    assert result["days"][2]["record_id"] is None
    late = store.summary(parent["id"], days=3, as_of="2026-09-25T22:00:00+02:00")
    assert late["days"][2]["record_id"] == future["id"]
    assert late["completion_rate"] == pytest.approx(2 / 3)
    assert late["recording_rate"] == 1
    before = store.summary(parent["id"], days=2, as_of="2026-09-19T20:00:00Z")
    assert before["scheduled_days"] == 0
    assert before["recording_rate"] is None
    assert not any(day["scheduled"] for day in before["days"])


def test_timezone_schedule_boundaries_dst_and_archive_corrections(tmp_path):
    store = InterventionStore(tmp_path)
    parent = store.create_plan(
        plan(start_date="2026-10-24", end_date="2026-10-26", weekdays=[6])
    )
    row = store.record(
        parent["id"], record(date="2026-10-25", observed_at="2026-10-24T23:30:00Z")
    )
    result = store.summary(parent["id"], days=3, as_of="2026-10-26T12:00:00Z")
    assert [day["date"] for day in result["days"]] == [
        "2026-10-24",
        "2026-10-25",
        "2026-10-26",
    ]
    assert [day["scheduled"] for day in result["days"]] == [False, True, False]
    assert result["completed_days"] == 1
    assert result["scheduled_days"] == 1
    with pytest.raises(MeasurementError, match="outside"):
        store.record(
            parent["id"],
            record(
                request_id="offday",
                date="2026-10-26",
                observed_at="2026-10-26T12:00:00Z",
            ),
        )
    archived = store.update_plan(
        parent["id"], dict(request_id="archive", revision=1, archived=True)
    )
    assert store.list_plans() == []
    assert store.list_plans(include_archived=True) == [archived]
    with pytest.raises(MeasurementError, match="archived"):
        store.record(parent["id"], record(request_id="archived"))
    corrected = store.correct_record(
        row["id"], dict(request_id="after-archive", revision=1, status="skipped")
    )
    assert corrected["status"] == "skipped"
    assert (
        store.summary(parent["id"], days=3, as_of="2026-10-26T12:00:00Z")[
            "skipped_days"
        ]
        == 1
    )
    unarchived = store.update_plan(
        parent["id"], dict(request_id="unarchive", revision=2, archived=False)
    )
    assert store.list_plans() == [unarchived]
    assert len(store.history_plan(parent["id"])) == 3


@pytest.mark.parametrize(
    "changes",
    [
        dict(kind="unknown"),
        dict(name=""),
        dict(instructions=""),
        dict(source=""),
        dict(timezone="Unknown/Zone"),
        dict(timezone=None),
        dict(start_date="20260920"),
        dict(start_date="2026-02-30"),
        dict(end_date="2026-09-19"),
        dict(weekdays=[]),
        dict(weekdays=[1, 1]),
        dict(weekdays=[True]),
        dict(weekdays=[7]),
        dict(weekdays="daily"),
        dict(extra=1),
    ],
)
def test_invalid_plans_leave_store_empty(tmp_path, changes):
    store = InterventionStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create_plan(plan(**changes))
    assert store.list_plans(include_archived=True) == []


@pytest.mark.parametrize(
    "changes",
    [
        dict(date="2026-09-19"),
        dict(date="invalid"),
        dict(status="unknown"),
        dict(observed_at="2026-09-24T20:00:00"),
        dict(observed_at="2026-09-23T20:00:00Z"),
        dict(notes=None),
        dict(notes="x" * 4001),
        dict(request_id=""),
        dict(source="changed"),
    ],
)
def test_invalid_observations_cannot_claim_day(tmp_path, changes):
    store = InterventionStore(tmp_path)
    parent = store.create_plan(plan())
    with pytest.raises(MeasurementError):
        store.record(parent["id"], record(**changes))
    assert store.list_records(parent["id"]) == []
    assert store.record(parent["id"], record())["revision"] == 1


def test_real_concurrency_cross_home_and_input_errors(tmp_path):
    store = InterventionStore(tmp_path / "one")
    with ThreadPoolExecutor(max_workers=4) as pool:
        plans = list(
            pool.map(
                lambda _: InterventionStore(tmp_path / "one").create_plan(plan()),
                range(4),
            )
        )
    assert plans.count(plans[0]) == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(
                lambda _: InterventionStore(tmp_path / "one").record(
                    plans[0]["id"], record()
                ),
                range(4),
            )
        )
    assert rows.count(rows[0]) == 4
    assert len(store.list_records(plans[0]["id"])) == 1
    other = InterventionStore(tmp_path / "two")
    assert other.list_plans() == []
    for method, identity in [
        (other.get_plan, plans[0]["id"]),
        (other.history_plan, plans[0]["id"]),
        (other.list_records, plans[0]["id"]),
        (other.get_record, rows[0]["id"]),
        (other.history_record, rows[0]["id"]),
        (other.summary, plans[0]["id"]),
    ]:
        with pytest.raises(MeasurementError) as caught:
            method(identity)
        assert caught.value.status == 404
    for value in [0, 367, True, "30"]:
        with pytest.raises(MeasurementError, match="days"):
            store.summary(plans[0]["id"], days=value)
    with pytest.raises(MeasurementError, match="boolean"):
        store.list_plans(include_archived="true")
    with pytest.raises(MeasurementError, match="archive"):
        store.update_plan(
            plans[0]["id"], dict(request_id="bad-archive", revision=1, archived="true")
        )


@pytest.mark.asyncio
async def test_real_http_lifecycle_and_validation(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/wellbeing/interventions"
        response = await client.post(base + "/plans", json=plan())
        assert response.status == 200
        parent = await response.json()
        path = base + "/plans/" + parent["id"]
        response = await client.get(path)
        assert await response.json() == parent
        response = await client.get(base + "/plans")
        assert (await response.json())["plans"] == [parent]
        response = await client.post(path + "/records", json=record())
        assert response.status == 200
        row = await response.json()
        response = await client.get(path + "/records")
        assert (await response.json())["records"] == [row]
        item = base + "/records/" + row["id"]
        response = await client.put(
            item, json=dict(request_id="correct", revision=1, status="skipped")
        )
        assert response.status == 200
        updated = await response.json()
        response = await client.get(item)
        assert await response.json() == updated
        response = await client.get(item + "/history")
        assert (await response.json())["history"] == [row, updated]
        response = await client.get(
            path + "/summary", params={"days": 3, "as_of": "2026-09-25T12:00:00+02:00"}
        )
        summary = await response.json()
        assert summary["skipped_days"] == 1
        assert summary["unrecorded_days"] == 2
        response = await client.put(
            path, json=dict(request_id="archive", revision=1, archived=True)
        )
        assert response.status == 200
        response = await client.get(path + "/history")
        assert len((await response.json())["history"]) == 2
        response = await client.get(base + "/plans")
        assert (await response.json())["plans"] == []
        response = await client.get(base + "/plans?include_archived=true")
        assert len((await response.json())["plans"]) == 1
        for suffix in [
            "/plans?include_archived=invalid",
            "/plans/" + parent["id"] + "/summary?days=invalid",
        ]:
            assert (await client.get(base + suffix)).status == 400
        assert (
            await client.post(
                base + "/plans",
                data="broken",
                headers={"Content-Type": "application/json"},
            )
        ).status == 400
        assert (await client.get(base + "/plans/missing")).status == 404


@pytest.mark.asyncio
async def test_native_operations_share_ledger_and_revisions(tmp_path):
    provider = WellbeingProvider(tmp_path)

    async def invoke(name, identity=None, data=None):
        result = await provider.invoke(
            "wellbeing_records",
            dict(operation="intervention_" + name, id=identity, payload=data or {}),
        )
        assert result.success, result.error
        return json.loads(result.output)

    parent = await invoke("create_plan", data=plan())
    assert await invoke("list_plans") == [parent]
    assert await invoke("get_plan", parent["id"]) == parent
    updated = await invoke(
        "update_plan", parent["id"], dict(request_id="rename", revision=1, name="Walk")
    )
    assert await invoke("history_plan", parent["id"]) == [parent, updated]
    row = await invoke("record", parent["id"], record())
    assert row["plan_revision"] == 2
    assert await invoke("get_record", row["id"]) == row
    assert await invoke("list_records", parent["id"]) == [row]
    corrected = await invoke(
        "correct_record",
        row["id"],
        dict(request_id="correction", revision=1, status="skipped"),
    )
    assert await invoke("history_record", row["id"]) == [row, corrected]
    result = await invoke(
        "summary", parent["id"], dict(days=3, as_of="2026-09-25T12:00:00Z")
    )
    assert result["skipped_days"] == 1
    assert result["completed_days"] == 0
    assert result["unrecorded_days"] == 2
    assert InterventionStore(tmp_path).get_record(row["id"]) == corrected
    failed = await provider.invoke(
        "wellbeing_records", dict(operation="intervention_missing")
    )
    assert not failed.success
    assert "Unknown" in failed.error
    assert (
        "intervention_record"
        in (await provider.list_tools())[0].parameters["properties"]["operation"][
            "enum"
        ]
    )
