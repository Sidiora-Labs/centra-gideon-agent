import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.workspace.capabilities.wellbeing.body_composition import BodyCompositionStore
from gideon.workspace.capabilities.wellbeing.body_composition_http import BASE, register
from gideon.workspace.capabilities.wellbeing.body_composition_provider import BodyCompositionProvider
from gideon.workspace.capabilities.wellbeing.store import MeasurementError


def authored(**changes):
    payload = {
        "request_id": "body-one",
        "observed_at": "2026-09-25T08:30:00+02:00",
        "source": "User-authored smart scale transcription",
        "values": {
            "muscle_percent": 41.2,
            "fat_percent": 18.4,
            "bone_mass": {"value": 6.6, "unit": "lb"},
            "temperature": {"value": 98.6, "unit": "F"},
        },
        "notes": "Morning observation",
    }
    payload.update(changes)
    return payload


def test_store_retains_originals_normalizes_units_and_preserves_history(tmp_path):
    store = BodyCompositionStore(tmp_path)
    record = store.create(authored())
    assert record["revision"] == 1
    assert record["source"] == "User-authored smart scale transcription"
    assert record["observed_at"] == "2026-09-25T08:30:00+02:00"
    assert record["original_values"] == authored()["values"]
    assert record["normalized_values"] == {
        "muscle_percent": 41.2,
        "fat_percent": 18.4,
        "bone_mass_kg": 2.993709642,
        "temperature_c": 37.0,
    }
    assert "weight" not in json.dumps(record).lower()
    assert store.create(authored()) == record
    with pytest.raises(MeasurementError, match="another"):
        store.create(authored(notes="Different", request_id="body-one"))
    corrected = store.correct(record["id"], {
        "request_id": "body-correction", "revision": 1,
        "observed_at": record["observed_at"], "notes": "Corrected from paper record",
        "values": {
            "muscle_percent": 40.9, "fat_percent": 18.7,
            "bone_mass": {"value": 2994, "unit": "g"},
            "temperature": {"value": 310.15, "unit": "K"},
        },
    })
    assert corrected["revision"] == 2
    assert corrected["source"] == record["source"]
    assert corrected["created_at"] == record["created_at"]
    assert corrected["original_values"]["bone_mass"] == {"value": 2994.0, "unit": "g"}
    assert corrected["original_values"]["temperature"] == {"value": 310.15, "unit": "K"}
    assert corrected["normalized_values"]["bone_mass_kg"] == 2.994
    assert corrected["normalized_values"]["temperature_c"] == 37.0
    assert store.history(record["id"]) == [record, corrected]
    assert store.list() == [corrected]
    assert store.list(from_date="2026-09-25T06:30:01Z") == []
    exported = store.export()
    assert exported == {"schema_version": 1, "body_composition": [corrected], "history": [record, corrected]}
    with pytest.raises(MeasurementError, match="changed"):
        store.correct(record["id"], {"request_id": "stale", "revision": 1, "notes": "stale"})
    assert BodyCompositionStore(tmp_path).history(record["id"]) == [record, corrected]
    with sqlite3.connect(tmp_path / "capabilities/wellbeing.sqlite3") as database:
        assert database.execute("SELECT count(*) FROM body_composition_revisions").fetchone()[0] == 2
        assert database.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0


@pytest.mark.parametrize("values", [
    {"muscle_percent": 41, "fat_percent": 18, "bone_mass": {"value": 3, "unit": "stone"}, "temperature": {"value": 37, "unit": "C"}},
    {"muscle_percent": 41, "fat_percent": 18, "bone_mass": {"value": 3, "unit": "kg"}, "temperature": {"value": 37, "unit": "R"}},
    {"muscle_percent": 90, "fat_percent": 20, "bone_mass": {"value": 3, "unit": "kg"}, "temperature": {"value": 37, "unit": "C"}},
    {"muscle_percent": True, "fat_percent": 18, "bone_mass": {"value": 3, "unit": "kg"}, "temperature": {"value": 37, "unit": "C"}},
    {"muscle_percent": 41, "fat_percent": 18, "bone_mass": {"value": 3, "unit": "kg"}, "temperature": {"value": -1, "unit": "K"}},
    {"muscle_percent": 41, "fat_percent": 18, "bone_mass": {"value": 3, "unit": "kg"}, "temperature": {"value": 37, "unit": "C"}, "weight": 70},
])
def test_invalid_or_weight_duplicating_values_never_persist(tmp_path, values):
    store = BodyCompositionStore(tmp_path)
    with pytest.raises(MeasurementError):
        store.create(authored(values=values))
    assert store.list() == []


async def source_client(home):
    app = web.Application()
    register(app, home)
    result = TestClient(TestServer(app))
    await result.start_server()
    return result


@pytest.mark.asyncio
async def test_source_http_two_home_crud_history_export_and_reopen(tmp_path):
    first = await source_client(tmp_path / "first")
    second = await source_client(tmp_path / "second")
    try:
        response = await first.post(BASE, json=authored())
        assert response.status == 201
        created = await response.json()
        assert created["normalized_values"]["temperature_c"] == 37.0
        resource = BASE + "/" + created["id"]
        response = await first.post(BASE, json=authored())
        assert response.status == 201 and await response.json() == created
        response = await second.get(BASE)
        assert response.status == 200 and await response.json() == {"records": []}
        response = await second.get(resource)
        assert response.status == 404 and (await response.json())["code"] == "not_found"
        correction = {
            "request_id": "http-correct", "revision": 1, "notes": "Paper correction",
            "values": {"muscle_percent": 42, "fat_percent": 18, "bone_mass": {"value": 3, "unit": "kg"}, "temperature": {"value": 37, "unit": "C"}},
        }
        response = await first.put(resource, json=correction)
        assert response.status == 200
        updated = await response.json()
        assert updated["revision"] == 2
        assert updated["observed_at"] == created["observed_at"] and updated["source"] == created["source"]
        response = await first.put(resource, json={**correction, "request_id": "stale"})
        assert response.status == 409 and (await response.json())["code"] == "conflict"
        response = await first.get(resource + "/history")
        assert response.status == 200 and await response.json() == {"history": [created, updated]}
        response = await first.get(BASE + "/export")
        exported = await response.json()
        assert response.status == 200 and exported["body_composition"] == [updated]
        assert exported["history"] == [created, updated]
        await first.close()
        first = await source_client(tmp_path / "first")
        response = await first.get(resource)
        assert response.status == 200 and await response.json() == updated
    finally:
        await first.close()
        await second.close()


def test_native_tools_use_actual_store_and_mark_writes_for_approval(tmp_path):
    async def journey():
        provider = BodyCompositionProvider(tmp_path)
        tools = {tool.name: tool for tool in await provider.list_tools()}
        assert tools["body_composition_read"].requires_approval is False
        assert tools["body_composition_write"].requires_approval is True
        assert tools["body_composition_write"].risk_level.value == "caution"
        created = await provider.invoke("body_composition_write", {"operation": "create", "payload": authored(request_id="native-create")})
        assert created.success is True
        record = json.loads(created.output)
        read = await provider.invoke("body_composition_read", {"operation": "get", "id": record["id"]})
        assert read.success is True and json.loads(read.output) == record
        corrected = await provider.invoke("body_composition_write", {"operation": "correct", "id": record["id"], "payload": {"request_id": "native-correct", "revision": 1, "notes": "Native correction"}})
        assert corrected.success is True and json.loads(corrected.output)["revision"] == 2
        history = await provider.invoke("body_composition_read", {"operation": "history", "id": record["id"]})
        assert [row["revision"] for row in json.loads(history.output)] == [1, 2]
        exported = await provider.invoke("body_composition_read", {"operation": "export"})
        assert len(json.loads(exported.output)["history"]) == 2
        refused = await provider.invoke("body_composition_write", {"operation": "dispatch", "payload": {}})
        assert refused.success is False
    asyncio.run(journey())


def test_native_manifest_points_to_the_approval_aware_provider():
    path = Path("runtime/gideon/extensions/apps/native/gideon-body-composition/app.json")
    manifest = json.loads(path.read_text())
    assert manifest["name"] == "gideon-body-composition"
    assert manifest["native"] is True
    assert manifest["provider"] == {
        "type": "tool",
        "implementation": "gideon.workspace.capabilities.wellbeing.body_composition_provider:create_provider",
        "capabilities": ["body-composition-records"],
    }
    assert "wellbeing" in manifest["tags"]
    assert "health" in manifest["tags"]
