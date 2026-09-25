import asyncio
import json
import sqlite3

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.interfaces.dashboard.token_auth import generate_token, token_auth_middleware, use_ephemeral_secret
from gideon.workspace.capabilities.wellbeing.eyes import EyePrescriptionStore, SCHEMA
from gideon.workspace.capabilities.wellbeing.eyes_http import register
from gideon.workspace.capabilities.wellbeing.eyes_provider import EyePrescriptionTools, create_provider


BASE = "/api/capabilities/wellbeing/eyes"


def eye(sphere=-1.25, cylinder=-0.5, axis=90):
    return {"sphere": sphere, "sphere_unit": "D", "cylinder": cylinder,
            "cylinder_unit": "D", "axis": axis, "axis_unit": "degrees"}


def prescription(**changes):
    value = {"request_id": "eyes-create-1", "observed_date": "2026-09-24",
             "source": "Optometrist paper prescription", "notes": "Authored copy",
             "left": eye(), "right": eye(-1.0, -0.25, 80)}
    value.update(changes)
    return value


async def client(home, identity):
    app = web.Application(middlewares=[token_auth_middleware()])
    register(app, home)
    result = TestClient(TestServer(app))
    await result.start_server()
    assert (await result.get(BASE)).status in {401, 403}
    response = await result.get(BASE, params={"token": generate_token(identity)})
    assert response.status == 200
    return result


async def call(client, method, path, payload=None):
    response = await client.request(method, path, json=payload)
    return response.status, await response.json()


def test_two_home_create_correct_history_export_replay_and_restart(tmp_path):
    async def journey():
        use_ephemeral_secret()
        first = await client(tmp_path / "first", "eyes-first")
        second = await client(tmp_path / "second", "eyes-second")
        try:
            payload = prescription()
            status, original = await call(first, "POST", BASE, payload)
            assert status == 201
            assert original["revision"] == 1
            assert original["observed_date"] == "2026-09-24"
            assert original["source"] == "Optometrist paper prescription"
            assert original["notes"] == "Authored copy"
            assert original["left"] == eye()
            assert original["right"] == eye(-1.0, -0.25, 80)
            assert original["left"]["sphere_unit"] == "D"
            assert original["left"]["cylinder_unit"] == "D"
            assert original["left"]["axis_unit"] == "degrees"
            assert set(original) == {"id", "observed_date", "source", "notes", "left", "right",
                                     "revision", "created_at", "updated_at"}

            status, replay = await call(first, "POST", BASE, payload)
            assert status == 201
            assert replay == original
            status, conflict = await call(first, "POST", BASE, prescription(notes="changed replay"))
            assert status == 409
            assert conflict["code"] == "conflict"

            resource = BASE + "/" + original["id"]
            status, read = await call(first, "GET", resource)
            assert status == 200
            assert read == original
            status, listing = await call(first, "GET", BASE + "?from=2026-09-24&to=2026-09-24")
            assert status == 200
            assert listing == {"prescriptions": [original]}
            status, outside = await call(first, "GET", BASE + "?from=2026-09-25")
            assert status == 200
            assert outside == {"prescriptions": []}

            status, isolated = await call(second, "GET", BASE)
            assert status == 200
            assert isolated == {"prescriptions": []}
            status, hidden = await call(second, "GET", resource)
            assert status == 404
            assert hidden["code"] == "not_found"

            correction = {"request_id": "eyes-correct-1", "revision": 1, "observed_date": "2026-09-25",
                          "notes": "Cylinder transcription corrected", "left": eye(-1.25, -0.75, 95),
                          "right": eye(-1.0, -0.25, 80)}
            status, corrected = await call(first, "PUT", resource, correction)
            assert status == 200
            assert corrected["revision"] == 2
            assert corrected["observed_date"] == "2026-09-25"
            assert corrected["left"] == eye(-1.25, -0.75, 95)
            assert corrected["source"] == original["source"]
            assert corrected["created_at"] == original["created_at"]
            assert corrected["updated_at"] >= original["updated_at"]
            status, correction_replay = await call(first, "PUT", resource, correction)
            assert status == 200
            assert correction_replay == corrected

            stale = {**correction, "request_id": "eyes-stale", "notes": "stale"}
            status, stale_error = await call(first, "PUT", resource, stale)
            assert status == 409
            assert stale_error["code"] == "conflict"
            status, versions = await call(first, "GET", resource + "/history")
            assert status == 200
            assert versions == {"history": [original, corrected]}

            status, exported = await call(first, "GET", BASE + "/export")
            assert status == 200
            assert exported == {"schema": SCHEMA, "version": 1,
                                "prescriptions": [corrected], "history": [original, corrected]}
            assert not any(key in exported for key in ("diagnosis", "interpretation", "recommendation"))

            database_path = tmp_path / "first" / "capabilities/wellbeing.sqlite3"
            assert database_path.exists()
            with sqlite3.connect(database_path) as database:
                assert database.execute("SELECT count(*) FROM eye_prescription_revisions").fetchone() == (2,)
                assert database.execute("SELECT count(*) FROM eye_prescription_requests").fetchone() == (2,)
                assert database.execute("SELECT count(*) FROM revisions").fetchone() == (0,)

            await first.close()
            first = await client(tmp_path / "first", "eyes-reopen")
            status, reopened = await call(first, "GET", resource + "/history")
            assert status == 200
            assert reopened == versions
        finally:
            await first.close()
            await second.close()
    asyncio.run(journey())


@pytest.mark.parametrize("payload", [
    prescription(observed_date="2026-9-1"),
    prescription(observed_date="2026-02-30"),
    prescription(left=eye(float("nan"))),
    prescription(left=eye(-41)),
    prescription(left=eye(cylinder=21)),
    prescription(left=eye(axis=-1)),
    prescription(left=eye(axis=181)),
    prescription(left={"sphere": -1, "sphere_unit": "m", "cylinder": 0, "cylinder_unit": "D", "axis": 90, "axis_unit": "degrees"}),
    prescription(right={"sphere": -1, "sphere_unit": "D", "cylinder": 0, "cylinder_unit": "D", "axis": 90}),
    prescription(home="/tmp/escape"),
    prescription(source=""),
    prescription(notes="x" * 4001),
])
def test_invalid_authored_values_are_atomic(tmp_path, payload):
    async def journey():
        use_ephemeral_secret()
        connection = await client(tmp_path, "eyes-invalid")
        try:
            status, error = await call(connection, "POST", BASE, payload)
            assert status == 400
            assert error["code"] == "invalid_request"
            status, records = await call(connection, "GET", BASE)
            assert status == 200
            assert records == {"prescriptions": []}
        finally:
            await connection.close()
    asyncio.run(journey())


def test_corrections_keep_source_immutable_and_queries_are_bounded(tmp_path):
    async def journey():
        use_ephemeral_secret()
        connection = await client(tmp_path, "eyes-corrections")
        try:
            status, record = await call(connection, "POST", BASE, prescription())
            assert status == 201
            resource = BASE + "/" + record["id"]
            changes = {"request_id": "source-change", "revision": 1, "source": "another clinic"}
            status, immutable = await call(connection, "PUT", resource, changes)
            assert status == 400
            assert "immutable" in immutable["error"]
            status, unchanged = await call(connection, "GET", resource)
            assert status == 200
            assert unchanged == record
            for query in ("?home=/tmp/other", "?limit=0", "?limit=501", "?offset=-1",
                          "?from=2026-09-26&to=2026-09-25", "?from=yesterday"):
                status, error = await call(connection, "GET", BASE + query)
                assert status == 400
                assert error["code"] == "invalid_request"
        finally:
            await connection.close()
    asyncio.run(journey())


def test_native_manifest_provider_reads_same_store_and_gates_writes(tmp_path):
    async def journey():
        store = EyePrescriptionStore(tmp_path)
        provider = EyePrescriptionTools(store)
        definitions = {item.name: item for item in await provider.list_tools()}
        assert set(definitions) == {
            "wellbeing_eye_prescriptions_list", "wellbeing_eye_prescriptions_get",
            "wellbeing_eye_prescriptions_history", "wellbeing_eye_prescriptions_export",
            "wellbeing_eye_prescriptions_create", "wellbeing_eye_prescriptions_correct",
        }
        writes = {"wellbeing_eye_prescriptions_create", "wellbeing_eye_prescriptions_correct"}
        assert all(definitions[name].requires_approval is (name in writes) for name in definitions)
        assert all(definitions[name].risk_level.value == ("caution" if name in writes else "safe") for name in definitions)

        created_result = await provider.invoke("wellbeing_eye_prescriptions_create", {"payload": prescription()})
        assert created_result.success
        created = json.loads(created_result.output)
        listed = await provider.invoke("wellbeing_eye_prescriptions_list", {})
        assert json.loads(listed.output) == [created]
        read = await provider.invoke("wellbeing_eye_prescriptions_get", {"id": created["id"]})
        assert json.loads(read.output) == created
        correction = {"request_id": "native-correction", "revision": 1, "notes": "Native correction"}
        corrected_result = await provider.invoke("wellbeing_eye_prescriptions_correct", {"id": created["id"], "payload": correction})
        assert corrected_result.success
        corrected = json.loads(corrected_result.output)
        assert corrected["revision"] == 2
        history = await provider.invoke("wellbeing_eye_prescriptions_history", {"id": created["id"]})
        assert json.loads(history.output) == [created, corrected]
        exported = await provider.invoke("wellbeing_eye_prescriptions_export", {})
        assert json.loads(exported.output)["prescriptions"] == [corrected]
        refused = await provider.invoke("wellbeing_eye_prescriptions_correct", {"id": created["id"], "payload": {**correction, "notes": "changed replay"}})
        assert not refused.success
        assert refused.metadata["status"] == 409
    asyncio.run(journey())


def test_native_manifest_is_unique_and_points_to_provider():
    from pathlib import Path
    path = Path(__file__).resolve().parents[4] / "runtime/gideon/extensions/apps/native/gideon-wellbeing-eyes/app.json"
    manifest = json.loads(path.read_text())
    assert manifest["name"] == "gideon-wellbeing-eyes"
    assert manifest["native"] is True
    assert manifest["provider"] == {
        "type": "tool",
        "implementation": "gideon.workspace.capabilities.wellbeing.eyes_provider:create_provider",
        "capabilities": ["wellbeing-eye-prescriptions"],
    }
    with pytest.raises(ValueError, match="configuration"):
        create_provider({"home": "/tmp/not-allowed"})


def test_direct_store_orders_dates_pages_records_and_exports_every_authored_revision(tmp_path):
    store = EyePrescriptionStore(tmp_path)
    older = store.create(prescription(request_id="older", observed_date="2024-01-02", notes="Older"))
    newest = store.create(prescription(request_id="newest", observed_date="2026-09-25", notes="Newest"))
    middle = store.create(prescription(request_id="middle", observed_date="2025-06-10", notes="Middle"))

    assert store.list() == [newest, middle, older]
    assert store.list(limit=1) == [newest]
    assert store.list(limit=1, offset=1) == [middle]
    assert store.list(from_date="2025-01-01", to_date="2025-12-31") == [middle]

    corrected = store.correct(middle["id"], {
        "request_id": "middle-correction",
        "revision": 1,
        "notes": "Middle corrected",
        "right": eye(-2.0, -1.0, 120),
    })
    assert corrected["revision"] == 2
    assert corrected["right"] == eye(-2.0, -1.0, 120)
    assert store.history(middle["id"]) == [middle, corrected]
    assert store.list() == [newest, corrected, older]

    exported = store.export()
    assert exported["schema"] == SCHEMA
    assert exported["version"] == 1
    assert {row["id"]: row for row in exported["prescriptions"]} == {
        older["id"]: older,
        newest["id"]: newest,
        middle["id"]: corrected,
    }
    assert len(exported["history"]) == 4
    assert [row["revision"] for row in exported["history"] if row["id"] == middle["id"]] == [1, 2]


def test_native_provider_rejects_undeclared_shapes_without_writing(tmp_path):
    async def journey():
        store = EyePrescriptionStore(tmp_path)
        provider = EyePrescriptionTools(store)
        cases = [
            ("wellbeing_eye_prescriptions_list", {"home": "/tmp/escape"}),
            ("wellbeing_eye_prescriptions_get", {}),
            ("wellbeing_eye_prescriptions_create", {"payload": prescription(), "extra": True}),
            ("wellbeing_eye_prescriptions_correct", {"id": "missing"}),
            ("wellbeing_eye_prescriptions_unknown", {}),
        ]
        for name, arguments in cases:
            result = await provider.invoke(name, arguments)
            assert result.success is False
            assert "declared" in result.error
        assert store.list() == []
    asyncio.run(journey())
