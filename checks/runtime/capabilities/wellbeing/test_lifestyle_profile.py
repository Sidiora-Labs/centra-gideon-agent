import json
from pathlib import Path

import pytest

from gideon.core.sqlite_compat import sqlite3
from gideon.extensions.apps.manifest import AppManifest
from gideon.workspace.capabilities.wellbeing.lifestyle_profile import (
    SCHEMA,
    LifestyleProfileStore,
)
from gideon.workspace.capabilities.wellbeing.lifestyle_profile_provider import (
    LifestyleProfileProvider,
    create_provider,
)
from gideon.workspace.capabilities.wellbeing.store import (
    MeasurementError,
    MeasurementStore,
)


def observation(**changes):
    value = {
        "request_id": "profile-1",
        "observed_at": "2026-09-25T08:30:00+02:00",
        "source": "Owner-authored intake",
        "reported_sex": "female",
        "sex_source": "Owner report",
        "smoking_status": "former",
        "diet_quality": {
            "value": 7,
            "scale": {"minimum": 0, "maximum": 10, "label": "0–10 owner rating"},
        },
        "stress": {
            "value": 3,
            "scale": {"minimum": 0, "maximum": 10, "label": "0–10 owner rating"},
        },
        "reported_bmi": 23.4,
        "condition_labels": ["Asthma", "Migraine"],
        "reported_daily_alcohol": {"value": 0, "unit": "standard_drinks_per_day"},
    }
    value.update(changes)
    return value


def test_create_replay_correction_history_export_and_restart_share_wellbeing_database(
    tmp_path,
):
    store = LifestyleProfileStore(tmp_path)
    payload = observation()
    first = store.create(payload)
    assert (
        first["revision"] == 1
        and first["reported_sex"] == "female"
        and first["sex_source"] == "Owner report"
    )
    assert first["reported_daily_alcohol"] == {
        "value": 0.0,
        "unit": "standard_drinks_per_day",
    }
    assert first["diet_quality"]["scale"] == {
        "minimum": 0.0,
        "maximum": 10.0,
        "label": "0–10 owner rating",
    }
    assert (
        store.create(payload) == first
        and store.path == MeasurementStore(tmp_path).path
        and store.path.name == "wellbeing.sqlite3"
    )
    patch = {
        "request_id": "profile-correct",
        "revision": 1,
        "smoking_status": "never",
        "stress": {
            "value": 4,
            "scale": {"minimum": 1, "maximum": 5, "label": "1–5 survey"},
        },
        "reported_bmi": 23.1,
        "condition_labels": ["Asthma"],
        "reported_daily_alcohol": None,
    }
    changed = store.correct(first["id"], patch)
    assert (
        changed["revision"] == 2
        and changed["smoking_status"] == "never"
        and changed["reported_daily_alcohol"] is None
    )
    assert changed["stress"] == {
        "value": 4.0,
        "scale": {"minimum": 1.0, "maximum": 5.0, "label": "1–5 survey"},
    }
    for field in (
        "id",
        "observed_at",
        "source",
        "reported_sex",
        "sex_source",
        "created_at",
    ):
        assert changed[field] == first[field]
    assert store.correct(first["id"], patch) == changed
    assert (
        store.history(first["id"]) == [first, changed]
        and LifestyleProfileStore(tmp_path).get(first["id"]) == changed
    )
    exported = store.export()
    assert exported == {
        "schema": SCHEMA,
        "schema_version": 1,
        "records": [changed],
        "history": [first, changed],
    }
    wire = json.dumps(exported)
    for absent in ("birth_date", "sleep", "consumption", "diagnosis", "requests"):
        assert absent not in wire
    with pytest.raises(MeasurementError) as stale:
        store.correct(
            first["id"], {"request_id": "stale", "revision": 1, "reported_bmi": 24}
        )
    assert stale.value.status == 409 and store.history(first["id"]) == [first, changed]


@pytest.mark.parametrize(
    "change",
    [
        {"observed_at": "2026-09-25"},
        {"source": ""},
        {"reported_sex": ""},
        {"sex_source": ""},
        {"smoking_status": "sometimes"},
        {"reported_bmi": 4.9},
        {"reported_bmi": float("nan")},
        {"condition_labels": ["Asthma", "asthma"]},
        {"condition_labels": ["x"] * 51},
        {
            "diet_quality": {
                "value": 11,
                "scale": {"minimum": 0, "maximum": 10, "label": "0–10"},
            }
        },
        {
            "stress": {
                "value": 2,
                "scale": {"minimum": 5, "maximum": 1, "label": "reversed"},
            }
        },
        {"reported_daily_alcohol": {"value": -1, "unit": "g_per_day"}},
        {"reported_daily_alcohol": {"value": 1, "unit": "drinks"}},
        {"birth_date": "1990-01-01"},
        {"sleep_hours": 8},
        {"actual_consumption": 1},
    ],
)
def test_invalid_or_duplicate_authorities_are_rejected_atomically(tmp_path, change):
    store = LifestyleProfileStore(tmp_path)
    payload = observation()
    payload.update(change)
    with pytest.raises(MeasurementError):
        store.create(payload)
    assert store.list() == []


def test_request_identity_and_correction_provenance_fail_closed(tmp_path):
    store = LifestyleProfileStore(tmp_path)
    first = store.create(observation())
    with pytest.raises(MeasurementError) as reused:
        store.create(observation(reported_bmi=30))
    assert reused.value.status == 409
    for patch in (
        {"request_id": "source-change", "revision": 1, "source": "other"},
        {
            "request_id": "date-change",
            "revision": 1,
            "observed_at": "2026-09-26T00:00:00Z",
        },
        {"request_id": "unknown", "revision": 1, "diagnosis": "healthy"},
    ):
        with pytest.raises(MeasurementError):
            store.correct(first["id"], patch)
    assert store.get(first["id"]) == first and store.history(first["id"]) == [first]


def test_declared_alcohol_units_zero_and_observation_order_are_preserved_without_consumption_entries(
    tmp_path,
):
    store = LifestyleProfileStore(tmp_path)
    grams = store.create(
        observation(
            request_id="grams",
            observed_at="2026-09-25T12:00:00Z",
            reported_daily_alcohol={"value": 14.5, "unit": "g_per_day"},
        )
    )
    millilitres = store.create(
        observation(
            request_id="millilitres",
            observed_at="2026-09-26T12:00:00Z",
            reported_daily_alcohol={"value": 17.75, "unit": "ml_ethanol_per_day"},
        )
    )
    zero = store.create(
        observation(
            request_id="zero",
            observed_at="2026-09-27T12:00:00Z",
            reported_daily_alcohol={"value": 0, "unit": "standard_drinks_per_day"},
        )
    )
    absent = store.create(
        observation(
            request_id="absent",
            observed_at="2026-09-28T12:00:00Z",
            reported_daily_alcohol=None,
        )
    )
    assert grams["reported_daily_alcohol"] == {"value": 14.5, "unit": "g_per_day"}
    assert millilitres["reported_daily_alcohol"] == {
        "value": 17.75,
        "unit": "ml_ethanol_per_day",
    }
    assert zero["reported_daily_alcohol"] == {
        "value": 0.0,
        "unit": "standard_drinks_per_day",
    }
    assert absent["reported_daily_alcohol"] is None
    assert [row["id"] for row in store.list()] == [
        absent["id"],
        zero["id"],
        millilitres["id"],
        grams["id"],
    ]
    with sqlite3.connect(store.path) as database:
        tables = {
            row[0]
            for row in database.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "lifestyle_profile_revisions" in tables
    assert "substance_entries" not in tables
    assert "sleep_entries" not in tables


def test_every_mutable_authored_field_can_be_corrected_without_changing_observation_provenance(
    tmp_path,
):
    store = LifestyleProfileStore(tmp_path)
    original = store.create(observation())
    correction = {
        "request_id": "all-fields",
        "revision": 1,
        "reported_sex": "intersex",
        "sex_source": "Corrected owner report",
        "smoking_status": "current",
        "diet_quality": {
            "value": 4,
            "scale": {"minimum": 1, "maximum": 5, "label": "1–5 questionnaire"},
        },
        "stress": {
            "value": 70,
            "scale": {"minimum": 0, "maximum": 100, "label": "0–100 questionnaire"},
        },
        "reported_bmi": 25.2,
        "condition_labels": ["Asthma", "Eczema"],
        "reported_daily_alcohol": {"value": 1.25, "unit": "standard_drinks_per_day"},
    }
    changed = store.correct(original["id"], correction)
    assert changed["reported_sex"] == "intersex"
    assert changed["sex_source"] == "Corrected owner report"
    assert changed["smoking_status"] == "current"
    assert changed["diet_quality"]["value"] == 4
    assert changed["diet_quality"]["scale"]["label"] == "1–5 questionnaire"
    assert changed["stress"]["value"] == 70
    assert changed["stress"]["scale"]["maximum"] == 100
    assert changed["reported_bmi"] == 25.2
    assert changed["condition_labels"] == ["Asthma", "Eczema"]
    assert changed["reported_daily_alcohol"] == {
        "value": 1.25,
        "unit": "standard_drinks_per_day",
    }
    assert changed["observed_at"] == original["observed_at"]
    assert changed["source"] == original["source"]
    assert changed["created_at"] == original["created_at"]
    assert changed["updated_at"] != original["updated_at"]
    with pytest.raises(MeasurementError) as reused:
        store.correct(original["id"], {**correction, "reported_bmi": 30})
    assert reused.value.status == 409
    assert store.get(original["id"]) == changed


def test_export_is_canonical_data_only_and_does_not_expose_idempotency_ledger(tmp_path):
    store = LifestyleProfileStore(tmp_path)
    first = store.create(observation())
    second = store.correct(
        first["id"],
        {"request_id": "export-correction", "revision": 1, "reported_bmi": 22.8},
    )
    document = store.export()
    assert set(document) == {"schema", "schema_version", "records", "history"}
    assert document["records"] == [second]
    assert document["history"] == [first, second]
    encoded = json.dumps(document, sort_keys=True)
    assert "profile-1" not in encoded
    assert "export-correction" not in encoded
    assert "request_id" not in encoded
    assert "diagnosis" not in encoded
    with sqlite3.connect(store.path) as database:
        requests = database.execute("SELECT id FROM requests ORDER BY id").fetchall()
    assert requests == [("export-correction",), ("profile-1",)]


@pytest.mark.asyncio
async def test_native_provider_reads_same_store_exports_history_and_declares_write_approval(
    tmp_path,
):
    store = LifestyleProfileStore(tmp_path)
    provider = LifestyleProfileProvider(store)
    tools = {tool.name: tool for tool in await provider.list_tools()}
    assert set(tools) == {
        "lifestyle_profile_create",
        "lifestyle_profile_correct",
        "lifestyle_profile_get",
        "lifestyle_profile_history",
        "lifestyle_profile_list",
        "lifestyle_profile_export",
    }
    assert (
        tools["lifestyle_profile_create"].requires_approval
        and tools["lifestyle_profile_correct"].requires_approval
    )
    assert (
        not tools["lifestyle_profile_get"].requires_approval
        and not tools["lifestyle_profile_export"].requires_approval
    )
    created = await provider.invoke(
        "lifestyle_profile_create", {"payload": observation()}
    )
    assert created.success
    record = json.loads(created.output)
    fetched = await provider.invoke("lifestyle_profile_get", {"id": record["id"]})
    assert json.loads(fetched.output) == record
    corrected = await provider.invoke(
        "lifestyle_profile_correct",
        {
            "id": record["id"],
            "payload": {
                "request_id": "native-correct",
                "revision": 1,
                "reported_bmi": 22.9,
            },
        },
    )
    assert corrected.success
    history = await provider.invoke("lifestyle_profile_history", {"id": record["id"]})
    assert len(json.loads(history.output)) == 2
    exported = await provider.invoke("lifestyle_profile_export", {})
    assert json.loads(exported.output)["schema"] == SCHEMA
    refused = await provider.invoke("lifestyle_profile_diagnose", {})
    assert not refused.success
    with pytest.raises(ValueError):
        create_provider({"home": "/tmp/other"})
    manifest = AppManifest.from_json_file(
        Path(__file__).parents[4]
        / "runtime/gideon/extensions/apps/native/gideon-lifestyle-profile/app.json"
    )
    assert (
        manifest.name == "gideon-lifestyle-profile"
        and manifest.provider.implementation.endswith(
            "lifestyle_profile_provider:create_provider"
        )
    )
