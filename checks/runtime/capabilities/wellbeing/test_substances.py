import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.handlers.capabilities_wellbeing_substances import register
from gideon.workspace.capabilities.wellbeing.provider import WellbeingProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError
from gideon.workspace.capabilities.wellbeing.substances import ConsumptionStore


def entry(request_id="entry", **changes):
    value = dict(request_id=request_id, kind="alcohol", name="Recorded beer", observed_at="2026-09-25T09:00:00+02:00", count=2, details={"volume_ml": 330, "abv_percent": 5}, source="manual log", notes="with lunch")
    value.update(changes)
    return value


def nicotine(request_id="nicotine", **changes):
    value = entry(request_id, kind="nicotine", name="Labeled gum", count=3, details={"mg_per_unit": 2})
    value.update(changes)
    return value


def preset(request_id="preset", **changes):
    value = dict(request_id=request_id, kind="alcohol", name="Beer preset", details={"volume_ml": 330, "abv_percent": 5})
    value.update(changes)
    return value


def test_recorded_quantities_reopen_and_preserve_provenance(tmp_path):
    store = ConsumptionStore(tmp_path)
    alcohol = store.create_entry(entry())
    assert alcohol["ethanol_g"] == pytest.approx(26.037)
    assert alcohol["nicotine_mg"] is None
    assert alcohol["count"] == 2
    assert alcohol["details"] == {"volume_ml": 330, "abv_percent": 5}
    assert alcohol["observed_at"] == "2026-09-25T09:00:00+02:00"
    assert alcohol["source"] == "manual log"
    assert alcohol["revision"] == 1
    assert alcohol["deleted"] is False
    assert alcohol["preset_id"] is None
    labeled = store.create_entry(nicotine())
    assert labeled["nicotine_mg"] == 6
    assert labeled["ethanol_g"] is None
    reopened = ConsumptionStore(tmp_path)
    assert reopened.get_entry(alcohol["id"]) == alcohol
    assert reopened.get_entry(labeled["id"]) == labeled
    assert reopened.list_entries(kind="nicotine") == [labeled]
    assert reopened.history_entry(alcohol["id"]) == [alcohol]


def test_correction_replay_stale_write_and_tombstone_history(tmp_path):
    store = ConsumptionStore(tmp_path)
    original = store.create_entry(entry())
    assert store.create_entry(entry()) == original
    payload = dict(request_id="edit", revision=1, count=1.5, notes="count corrected", observed_at="2026-09-24T23:30:00Z")
    corrected = store.correct_entry(original["id"], payload)
    assert corrected["revision"] == 2
    assert corrected["ethanol_g"] == pytest.approx(19.52775)
    assert corrected["created_at"] == original["created_at"]
    assert corrected["source"] == original["source"]
    assert corrected["observed_at"] == "2026-09-24T23:30:00Z"
    assert store.correct_entry(original["id"], payload) == corrected
    with pytest.raises(MeasurementError) as failure:
        store.correct_entry(original["id"], dict(payload, request_id="stale"))
    assert failure.value.status == 409
    deleted = store.delete_entry(original["id"], dict(request_id="delete", revision=2))
    assert deleted["deleted"]
    assert deleted["revision"] == 3
    assert store.list_entries() == []
    assert store.history_entry(original["id"]) == [original, corrected, deleted]
    assert store.delete_entry(original["id"], dict(request_id="delete", revision=2)) == deleted
    assert store.create_entry(entry()) == original
    with pytest.raises(MeasurementError):
        store.correct_entry(original["id"], dict(request_id="after-delete", revision=3, count=1))


def test_presets_snapshot_details_and_survive_preset_edit_delete(tmp_path):
    store = ConsumptionStore(tmp_path)
    product = store.create_preset(preset())
    assert store.create_preset(preset()) == product
    snapshot = store.create_entry(dict(request_id="from-preset", preset_id=product["id"], count=1, observed_at="2026-09-25T12:00:00Z", source="manual preset", notes="original preset"))
    assert snapshot["preset_id"] == product["id"]
    assert snapshot["preset_revision"] == 1
    assert snapshot["name"] == product["name"]
    assert snapshot["details"] == product["details"]
    assert snapshot["ethanol_g"] == pytest.approx(13.0185)
    changed = store.update_preset(product["id"], dict(request_id="preset-edit", revision=1, details={"volume_ml": 500, "abv_percent": 6}, name="Larger product"))
    assert changed["revision"] == 2
    assert store.list_presets() == [changed]
    assert store.get_entry(snapshot["id"]) == snapshot
    second = store.create_entry(dict(request_id="second-preset", preset_id=product["id"], count=1, observed_at="2026-09-25T13:00:00Z", source="manual preset"))
    assert second["preset_revision"] == 2
    assert second["ethanol_g"] == pytest.approx(23.67)
    deleted = store.delete_preset(product["id"], dict(request_id="preset-delete", revision=2))
    assert deleted["deleted"]
    assert store.list_presets() == []
    assert ConsumptionStore(tmp_path).get_entry(snapshot["id"]) == snapshot
    with pytest.raises(MeasurementError) as failure:
        store.create_entry(dict(request_id="deleted-preset", preset_id=product["id"], count=1, observed_at="2026-09-25T14:00:00Z", source="manual"))
    assert failure.value.status == 409
    with pytest.raises(MeasurementError):
        store.create_entry(entry("mixed", preset_id=product["id"]))


def test_calendar_summary_distinguishes_unknown_from_recorded_zero(tmp_path):
    store = ConsumptionStore(tmp_path)
    empty = store.summary("Europe/Berlin", days=3, as_of="2026-09-25T12:00:00Z")
    assert empty["totals"] == {"ethanol_g": None, "nicotine_mg": None}
    assert empty["logged_days"] == {"alcohol": 0, "nicotine": 0}
    assert [day["date"] for day in empty["days"]] == ["2026-09-23", "2026-09-24", "2026-09-25"]
    zero = store.create_entry(entry("zero", details={"volume_ml": 330, "abv_percent": 0}, observed_at="2026-09-24T23:30:00Z"))
    store.create_entry(nicotine("zero-nicotine", details={"mg_per_unit": 0}))
    summary = store.summary("Europe/Berlin", days=3, as_of="2026-09-25T12:00:00Z")
    assert summary["totals"] == {"ethanol_g": 0, "nicotine_mg": 0}
    assert summary["logged_days"] == {"alcohol": 1, "nicotine": 1}
    assert summary["days"][0]["ethanol_g"] is None
    assert summary["days"][1]["ethanol_g"] is None
    assert summary["days"][2]["ethanol_g"] == 0
    assert summary["days"][2]["entry_count"] == 2
    assert summary["averages_per_logged_day"] == {"ethanol_g": 0, "nicotine_mg": 0}
    store.delete_entry(zero["id"], dict(request_id="remove-zero", revision=1))
    assert store.summary("Europe/Berlin", days=3, as_of="2026-09-25T12:00:00Z")["totals"]["ethanol_g"] is None


def test_summary_uses_local_days_excludes_future_and_deleted(tmp_path):
    store = ConsumptionStore(tmp_path)
    first = store.create_entry(entry("day-one", count=1, observed_at="2026-09-23T23:00:00Z"))
    second = store.create_entry(entry("day-two", count=2, observed_at="2026-09-24T23:00:00Z"))
    store.create_entry(entry("future", count=10, observed_at="2026-09-25T18:00:00Z"))
    store.create_entry(entry("too-old", count=10, observed_at="2026-09-22T23:00:00Z"))
    summary = store.summary("Europe/Berlin", days=2, as_of="2026-09-25T12:00:00Z")
    assert summary["totals"]["ethanol_g"] == pytest.approx(first["ethanol_g"] + second["ethanol_g"])
    assert summary["logged_days"]["alcohol"] == 2
    assert summary["averages_per_logged_day"]["ethanol_g"] == pytest.approx((first["ethanol_g"] + second["ethanol_g"]) / 2)
    utc_summary = store.summary("UTC", days=2, as_of="2026-09-25T12:00:00Z")
    assert utc_summary["totals"]["ethanol_g"] == second["ethanol_g"]
    assert utc_summary["days"][0]["entry_count"] == 1
    assert utc_summary["days"][1]["entry_count"] == 0
    dst = store.summary("Europe/Berlin", days=2, as_of="2026-10-25T12:00:00Z")
    assert [day["date"] for day in dst["days"]] == ["2026-10-24", "2026-10-25"]
    assert dst["totals"]["ethanol_g"] is None


@pytest.mark.parametrize("changes", [
    {"count": 0}, {"count": -1}, {"count": True}, {"count": "2"}, {"count": float("nan")},
    {"count": 1001}, {"kind": "unknown"}, {"name": ""}, {"name": "x" * 201},
    {"source": ""}, {"source": 12}, {"notes": None}, {"notes": "x" * 4001},
    {"observed_at": "2026-09-25"}, {"observed_at": "bad"}, {"request_id": ""},
    {"details": {}}, {"details": {"volume_ml": 0, "abv_percent": 5}},
    {"details": {"volume_ml": 5001, "abv_percent": 5}},
    {"details": {"volume_ml": 330, "abv_percent": 101}},
    {"details": {"volume_ml": 330, "abv_percent": -1}},
    {"details": {"volume_ml": 330, "abv_percent": float("inf")}},
    {"details": {"volume_ml": 330, "abv_percent": 5, "extra": 0}},
    {"id": "chosen"}, {"revision": 4}, {"created_at": "yesterday"},
])
def test_bad_entries_leave_no_history(tmp_path, changes):
    store = ConsumptionStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create_entry(entry(**changes))
    assert store.list_entries() == []
    assert store.summary(days=1)["totals"] == {"ethanol_g": None, "nicotine_mg": None}


@pytest.mark.parametrize("details", [{"mg_per_unit": -1}, {"mg_per_unit": 1001}, {"mg_per_unit": False}, {"mg_per_unit": "2"}, {"mg_per_unit": None}])
def test_nicotine_label_validation(tmp_path, details):
    with pytest.raises(MeasurementError):
        ConsumptionStore(tmp_path).create_entry(nicotine(details=details))


@pytest.mark.parametrize("changes", [{"source": "changed"}, {"kind": "nicotine"}, {"preset_id": "changed"}, {"created_at": "changed"}, {"revision": True}])
def test_provenance_cannot_be_replaced(tmp_path, changes):
    store = ConsumptionStore(tmp_path)
    original = store.create_entry(entry())
    update = dict(request_id="edit", revision=1, notes="edited")
    update.update(changes)
    with pytest.raises(MeasurementError):
        store.correct_entry(original["id"], update)
    assert store.history_entry(original["id"]) == [original]


def test_query_validation_and_isolation(tmp_path):
    store = ConsumptionStore(tmp_path / "first")
    original = store.create_entry(entry())
    assert store.list_entries(from_date="2026-09-25T07:00:00Z", to_date="2026-09-25T07:00:00.000000Z") == [original]
    for query in ({"kind": "other"}, {"limit": 0}, {"offset": -1}, {"from_date": "bad"}):
        with pytest.raises(MeasurementError):
            store.list_entries(**query)
    for query in ({"days": 0}, {"days": 367}, {"days": True}, {"timezone": "Invalid/Zone"}, {"as_of": "bad"}):
        with pytest.raises(MeasurementError):
            store.summary(**query)
    other = ConsumptionStore(tmp_path / "second")
    assert other.list_entries() == []
    assert other.list_presets() == []
    with pytest.raises(MeasurementError) as failure:
        other.get_entry(original["id"])
    assert failure.value.status == 404
    with pytest.raises(MeasurementError):
        other.history_entry(original["id"])


def test_concurrent_retries_and_optimistic_conflicts(tmp_path):
    ConsumptionStore(tmp_path)
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(lambda _: ConsumptionStore(tmp_path).create_entry(entry()), range(3)))
    assert all(row == rows[0] for row in rows)
    def edit(number):
        try:
            return ConsumptionStore(tmp_path).correct_entry(rows[0]["id"], dict(request_id=f"edit-{number}", revision=1, count=number + 1))
        except MeasurementError as failure:
            return failure.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(edit, range(2)))
    assert results.count(409) == 1
    assert len(ConsumptionStore(tmp_path).history_entry(rows[0]["id"])) == 2


@pytest.mark.asyncio
async def test_real_http_presets_entries_corrections_and_summary(tmp_path):
    app = web.Application()
    register(app, tmp_path)
    async with TestClient(TestServer(app)) as client:
        base = "/api/capabilities/wellbeing/substances"
        response = await client.post(base + "/presets", json=preset())
        assert response.status == 200
        product = await response.json()
        assert await (await client.get(base + "/presets")).json() == {"presets": [product]}
        response = await client.post(base + "/entries", json=dict(request_id="entry", preset_id=product["id"], count=1, observed_at="2026-09-25T09:00:00+02:00", source="manual"))
        assert response.status == 200
        original = await response.json()
        path = base + "/entries/" + original["id"]
        assert await (await client.get(path)).json() == original
        response = await client.put(path, json=dict(request_id="correct", revision=1, count=2))
        assert response.status == 200
        changed = await response.json()
        assert changed["ethanol_g"] == pytest.approx(26.037)
        assert await (await client.get(path + "/history")).json() == {"history": [original, changed]}
        response = await client.get(base + "/summary", params={"timezone": "Europe/Berlin", "days": "1", "as_of": "2026-09-25T12:00:00Z"})
        assert (await response.json())["totals"]["ethanol_g"] == changed["ethanol_g"]
        response = await client.put(base + "/presets/" + product["id"], json=dict(request_id="preset-edit", revision=1, name="Renamed"))
        assert response.status == 200
        assert (await response.json())["name"] == "Renamed"
        response = await client.delete(base + "/presets/" + product["id"], json=dict(request_id="preset-delete", revision=2))
        assert response.status == 200
        assert await (await client.get(path)).json() == changed
        response = await client.delete(path, json=dict(request_id="delete", revision=2))
        assert response.status == 200
        deleted = await response.json()
        assert deleted["deleted"]
        assert await (await client.get(base + "/entries")).json() == {"entries": []}
        assert len((await (await client.get(path + "/history")).json())["history"]) == 3


@pytest.mark.asyncio
async def test_native_operations_share_http_ledger(tmp_path):
    provider = WellbeingProvider(tmp_path)
    tool = "wellbeing_records"
    definition = (await provider.list_tools())[0]
    assert "substances_summary" in definition.parameters["properties"]["operation"]["enum"]
    result = await provider.invoke(tool, dict(operation="substances_preset_create", payload=preset()))
    product = json.loads(result.output)
    assert result.success
    result = await provider.invoke(tool, dict(operation="substances_preset_list"))
    assert json.loads(result.output) == [product]
    result = await provider.invoke(tool, dict(operation="substances_entry_create", payload=entry()))
    original = json.loads(result.output)
    result = await provider.invoke(tool, dict(operation="substances_entry_get", id=original["id"]))
    assert json.loads(result.output) == original
    result = await provider.invoke(tool, dict(operation="substances_entry_correct", id=original["id"], payload=dict(request_id="tool-correct", revision=1, count=1)))
    changed = json.loads(result.output)
    result = await provider.invoke(tool, dict(operation="substances_entry_history", id=original["id"]))
    assert json.loads(result.output) == [original, changed]
    result = await provider.invoke(tool, dict(operation="substances_entry_list"))
    assert json.loads(result.output) == [changed]
    result = await provider.invoke(tool, dict(operation="substances_summary", payload=dict(days=1, as_of="2026-09-25T12:00:00Z")))
    assert json.loads(result.output)["totals"]["ethanol_g"] == changed["ethanol_g"]
    result = await provider.invoke(tool, dict(operation="substances_entry_delete", id=original["id"], payload=dict(request_id="tool-delete", revision=2)))
    assert json.loads(result.output)["deleted"]
    result = await provider.invoke(tool, dict(operation="substances_preset_update", id=product["id"], payload=dict(request_id="product-edit", revision=1, name="Changed")))
    assert json.loads(result.output)["revision"] == 2
    result = await provider.invoke(tool, dict(operation="substances_preset_delete", id=product["id"], payload=dict(request_id="product-delete", revision=2)))
    assert json.loads(result.output)["deleted"]
    result = await provider.invoke(tool, dict(operation="substances_unknown"))
    assert not result.success
