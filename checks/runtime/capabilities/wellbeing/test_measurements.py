import asyncio
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import (
    MeasurementError,
    MeasurementStore,
)


def weight(request_id="weight-1", **changes):
    value = dict(
        request_id=request_id,
        kind="body_weight",
        observed_at="2026-09-25T09:30:00+02:00",
        unit="kg",
        values={"weight": 72.5},
        source="manual scale",
        notes="after breakfast",
    )
    value.update(changes)
    return value


def pressure(request_id="pressure-1", **changes):
    value = dict(
        request_id=request_id,
        kind="blood_pressure",
        observed_at="2026-09-25T09:35:00+02:00",
        unit="mmHg",
        values={"systolic": 120, "diastolic": 80},
        source="manual cuff",
        notes="seated",
    )
    value.update(changes)
    return value


def test_create_reopen_correct_and_export(tmp_path):
    store = MeasurementStore(tmp_path)
    original = store.create(weight())
    assert original["kind"] == "body_weight"
    assert original["values"] == {"weight": 72.5}
    assert original["unit"] == "kg"
    assert original["observed_at"] == "2026-09-25T09:30:00+02:00"
    assert original["source"] == "manual scale"
    assert original["notes"] == "after breakfast"
    assert original["revision"] == 1
    assert original["created_at"].endswith("+00:00")
    correction = dict(
        request_id="correction-1",
        revision=1,
        values={"weight": 71.8},
        notes="transcription corrected",
    )
    updated = MeasurementStore(tmp_path).correct(original["id"], correction)
    assert updated["id"] == original["id"]
    assert updated["revision"] == 2
    assert updated["source"] == original["source"]
    assert updated["created_at"] == original["created_at"]
    assert updated["values"] == {"weight": 71.8}
    assert updated["notes"] == "transcription corrected"
    reopened = MeasurementStore(tmp_path)
    assert reopened.get(original["id"]) == updated
    assert reopened.history(original["id"]) == [original, updated]
    assert reopened.export() == {
        "schema_version": 1,
        "measurements": [updated],
        "history": [original, updated],
    }


def test_pressure_and_normalized_weight(tmp_path):
    store = MeasurementStore(tmp_path)
    body = store.create(weight(unit="lb", values={"weight": 200}))
    assert body["unit"] == "kg"
    assert body["values"]["weight"] == pytest.approx(90.718474)
    bp = store.create(pressure())
    assert bp["unit"] == "mmHg"
    assert bp["values"] == {"systolic": 120, "diastolic": 80}
    assert bp["source"] == "manual cuff"
    assert store.list(kind="blood_pressure") == [bp]
    corrected = store.correct(
        body["id"],
        dict(
            request_id="pounds-correction",
            revision=1,
            unit="lb",
            values={"weight": 190},
        ),
    )
    assert corrected["values"]["weight"] == pytest.approx(86.1825503)
    assert corrected["unit"] == "kg"
    assert store.history(body["id"])[0] == body


def test_mutation_replay_returns_original_receipt(tmp_path):
    store = MeasurementStore(tmp_path)
    original = store.create(weight())
    assert store.create(weight()) == original
    changed = dict(request_id="correct", revision=1, notes="corrected")
    result = store.correct(original["id"], changed)
    assert store.correct(original["id"], changed) == result
    assert store.create(weight()) == original
    assert store.get(original["id"]) == result
    assert len(store.history(original["id"])) == 2
    with pytest.raises(MeasurementError) as failure:
        store.create(weight(notes="different content"))
    assert failure.value.status == 409
    with pytest.raises(MeasurementError) as stale:
        store.correct(
            original["id"], dict(request_id="new-correction", revision=1, notes="stale")
        )
    assert stale.value.code == "conflict"
    assert store.history(original["id"]) == [original, result]


@pytest.mark.parametrize(
    "changes",
    [
        {"unit": "stone"},
        {"kind": "sleep"},
        {"source": ""},
        {"source": 12},
        {"notes": None},
        {"notes": "n" * 4001},
        {"source": "s" * 257},
        {"observed_at": "2026-09-25"},
        {"observed_at": "2026-09-25T09:30:00"},
        {"observed_at": "2026-02-30T09:00:00Z"},
        {"observed_at": 0},
        {"values": {"weight": 0}},
        {"values": {"weight": -1}},
        {"values": {"weight": float("nan")}},
        {"values": {"weight": float("inf")}},
        {"values": {"weight": True}},
        {"values": {"weight": "72"}},
        {"values": {"weight": 1001}},
        {"values": {"weight": 72, "height": 180}},
        {"values": {}},
        {"values": None},
        {"request_id": ""},
        {"request_id": 1},
        {"id": "chosen-id"},
        {"revision": 10},
        {"created_at": "yesterday"},
    ],
)
def test_invalid_creation_leaves_no_record_or_receipt(tmp_path, changes):
    store = MeasurementStore(tmp_path)
    with pytest.raises(MeasurementError) as failure:
        store.create(weight(**changes))
    assert failure.value.status == 400
    assert store.export()["measurements"] == []
    assert store.export()["history"] == []
    valid = store.create(weight())
    assert valid["revision"] == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"unit": "kPa"},
        {"values": {"systolic": 120}},
        {"values": {"systolic": 401, "diastolic": 80}},
        {"values": {"systolic": 350, "diastolic": 301}},
        {"values": {"systolic": 80, "diastolic": 80}},
        {"values": {"systolic": 70, "diastolic": 80}},
        {"values": {"systolic": 120, "diastolic": False}},
    ],
)
def test_pressure_validation(tmp_path, changes):
    store = MeasurementStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create(pressure(**changes))
    assert store.list() == []


@pytest.mark.parametrize(
    "changes",
    [
        {"source": "other"},
        {"kind": "blood_pressure"},
        {"created_at": "other"},
        {"id": "other"},
        {"revision": True},
        {"unit": "lb"},
        {"values": {"weight": 0}},
        {"observed_at": "bad"},
    ],
)
def test_correction_guards_preserve_original(tmp_path, changes):
    store = MeasurementStore(tmp_path)
    original = store.create(weight())
    payload = dict(request_id="correction", revision=1, notes="edited")
    payload.update(changes)
    with pytest.raises(MeasurementError):
        store.correct(original["id"], payload)
    assert store.get(original["id"]) == original
    assert store.history(original["id"]) == [original]


def test_date_filters_use_instants_and_current_revision(tmp_path):
    store = MeasurementStore(tmp_path)
    earlier = store.create(weight("a", observed_at="2026-09-25T00:30:00+02:00"))
    later = store.create(weight("b", observed_at="2026-09-24T23:00:00Z"))
    assert store.list(
        from_date="2026-09-24T22:30:00Z", to_date="2026-09-24T22:30:00.000000Z"
    ) == [earlier]
    assert store.list(from_date="2026-09-24T22:30:00.000001Z") == [later]
    assert store.list(limit=1) == [later]
    assert store.list(limit=1, offset=1) == [earlier]
    corrected = store.correct(
        earlier["id"],
        dict(request_id="date-fix", revision=1, observed_at="2026-09-26T00:00:00Z"),
    )
    assert store.list(from_date="2026-09-25T00:00:00Z") == [corrected]
    assert store.list(to_date="2026-09-24T23:59:59Z") == [later]
    assert store.history(earlier["id"])[0]["observed_at"] == earlier["observed_at"]


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 501},
        {"limit": True},
        {"offset": -1},
        {"offset": 1000001},
        {"kind": "other"},
        {"from_date": "bad"},
        {"from_date": "2026-09-26T00:00:00Z", "to_date": "2026-09-25T00:00:00Z"},
    ],
)
def test_invalid_queries_are_errors(tmp_path, query):
    with pytest.raises(MeasurementError):
        MeasurementStore(tmp_path).list(**query)


def test_homes_and_missing_ids(tmp_path):
    first = MeasurementStore(tmp_path / "first")
    second = MeasurementStore(tmp_path / "second")
    row = first.create(weight())
    assert second.list() == []
    for operation in (second.get, second.history):
        with pytest.raises(MeasurementError) as failure:
            operation(row["id"])
        assert failure.value.status == 404
    with pytest.raises(MeasurementError) as failure:
        second.correct(
            row["id"], dict(request_id="edit", revision=1, notes="wrong home")
        )
    assert failure.value.status == 404
    assert second.export()["history"] == []
    assert first.get(row["id"]) == row


def test_concurrent_creation_and_correction_are_atomic(tmp_path):
    MeasurementStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(
            pool.map(lambda _: MeasurementStore(tmp_path).create(weight()), range(4))
        )
    assert all(row == rows[0] for row in rows)
    assert len(MeasurementStore(tmp_path).list()) == 1

    def correct(number):
        try:
            return MeasurementStore(tmp_path).correct(
                rows[0]["id"],
                dict(request_id=f"edit-{number}", revision=1, notes=str(number)),
            )
        except MeasurementError as exc:
            return exc.status

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(correct, range(2)))
    assert sum(isinstance(result, dict) for result in results) == 1
    assert results.count(409) == 1
    history = MeasurementStore(tmp_path).history(rows[0]["id"])
    assert [row["revision"] for row in history] == [1, 2]
    assert history[0] == rows[0]


@pytest.mark.asyncio
async def test_real_http_lifecycle_and_isolation(tmp_path):
    app, other = web.Application(), web.Application()
    register(app, tmp_path / "one")
    register(other, tmp_path / "two")
    async with (
        TestClient(TestServer(app)) as client,
        TestClient(TestServer(other)) as second,
    ):
        prefix = "/api/capabilities/wellbeing"
        response = await client.post(prefix + "/measurements", json=pressure())
        assert response.status == 200
        original = await response.json()
        path = prefix + "/measurements/" + original["id"]
        assert await (await client.get(path)).json() == original
        assert (await second.get(path)).status == 404
        assert await (await second.get(prefix + "/measurements")).json() == {
            "measurements": []
        }
        correction = dict(
            request_id="correct", revision=1, values={"systolic": 118, "diastolic": 78}
        )
        response = await client.put(path, json=correction)
        assert response.status == 200
        updated = await response.json()
        assert updated["revision"] == 2
        assert updated["values"] == {"systolic": 118, "diastolic": 78}
        assert await (await client.put(path, json=correction)).json() == updated
        assert await (await client.get(path + "/history")).json() == {
            "history": [original, updated]
        }
        response = await client.get(
            prefix + "/measurements",
            params={
                "kind": "blood_pressure",
                "from": "2026-09-25T07:35:00Z",
                "to": "2026-09-25T07:35:00Z",
            },
        )
        assert await response.json() == {"measurements": [updated]}
        exported = await (await client.get(prefix + "/export")).json()
        assert exported == {
            "schema_version": 1,
            "measurements": [updated],
            "history": [original, updated],
        }
    assert MeasurementStore(tmp_path / "one").get(original["id"]) == updated


@pytest.mark.asyncio
async def test_real_http_error_envelopes(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        path = "/api/capabilities/wellbeing/measurements"
        for payload in ([], None, {"unit": "kg"}, weight(values={"weight": 0})):
            response = await client.post(path, json=payload)
            assert response.status == 400
            assert (await response.json())["error"]["code"] == "invalid_request"
        response = await client.post(
            path, data="broken", headers={"Content-Type": "application/json"}
        )
        assert response.status == 400
        response = await client.post(
            path, data="{}", headers={"Content-Type": "text/plain"}
        )
        assert response.status == 400
        for query in ({"limit": "bad"}, {"offset": "-1"}, {"from": "not a date"}):
            response = await client.get(path, params=query)
            assert response.status == 400
        original = await (await client.post(path, json=weight())).json()
        response = await client.put(
            path + "/" + original["id"],
            json=dict(request_id="stale", revision=0, notes="stale"),
        )
        assert response.status == 409
        assert (await response.json())["error"]["code"] == "conflict"
        response = await client.get(path + "/unknown/history")
        assert response.status == 404
        assert (await response.json())["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_agent_tools_share_real_store(tmp_path):
    provider = WellbeingProvider(tmp_path)
    definition = (await provider.list_tools())[0]
    assert definition.name == "wellbeing_records"
    assert definition.requires_approval
    assert "correct" in definition.parameters["properties"]["operation"]["enum"]
    result = await provider.invoke(
        definition.name, dict(operation="create", payload=weight())
    )
    assert result.success
    original = json.loads(result.output)
    assert MeasurementStore(tmp_path).get(original["id"]) == original
    result = await provider.invoke(
        definition.name, dict(operation="list", payload={"kind": "body_weight"})
    )
    assert json.loads(result.output) == [original]
    result = await provider.invoke(
        definition.name, dict(operation="get", id=original["id"])
    )
    assert json.loads(result.output) == original
    result = await provider.invoke(
        definition.name,
        dict(
            operation="correct",
            id=original["id"],
            payload=dict(request_id="tool-edit", revision=1, notes="tool correction"),
        ),
    )
    updated = json.loads(result.output)
    assert updated["revision"] == 2
    result = await provider.invoke(
        definition.name, dict(operation="history", id=original["id"])
    )
    assert json.loads(result.output) == [original, updated]
    result = await provider.invoke(definition.name, dict(operation="export"))
    assert json.loads(result.output)["measurements"] == [updated]
    result = await provider.invoke("other", dict(operation="export"))
    assert not result.success
    result = await provider.invoke(definition.name, dict(operation="get", id="missing"))
    assert not result.success
    assert "not found" in result.error
    isolated = WellbeingProvider(tmp_path / "other")
    result = await isolated.invoke(definition.name, dict(operation="list"))
    assert json.loads(result.output) == []
