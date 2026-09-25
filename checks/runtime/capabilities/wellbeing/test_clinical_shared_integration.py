import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from gideon.core.sqlite_compat import sqlite3
from gideon.extensions.apps.manifest import AppManifest
from gideon.extensions.providers.registry import ProviderRegistry, ToolTypeHandler
from gideon.interfaces.dashboard.handlers.capabilities_wellbeing import register
from gideon.workspace.capabilities.wellbeing.exports import ExportStore

BASE = "/api/capabilities/wellbeing"
ROOT = Path(__file__).resolve().parents[4]
NATIVE = ROOT / "runtime/gideon/extensions/apps/native"


def eye(sphere, cylinder, axis):
    return {
        "sphere": sphere,
        "sphere_unit": "D",
        "cylinder": cylinder,
        "cylinder_unit": "D",
        "axis": axis,
        "axis_unit": "degrees",
    }


FAMILIES = {
    "epigenetic": {
        "path": BASE + "/epigenetic",
        "payload": {
            "request_id": "shared-epi",
            "source_report_id": "report-2026",
            "observed_at": "2026-09-20",
            "source": "Owner uploaded report",
            "biological_age": {"value": 38.4, "unit": "years"},
            "chronological_age": {"value": 41, "unit": "years"},
            "pace_of_aging": {"value": 0.91, "scale": "years/year"},
            "organ_scores": {},
            "notes": "Transcribed",
        },
        "manifest": "gideon-wellbeing-epigenetic",
        "tool": "wellbeing_epigenetic_export",
        "arguments": {},
        "section": "epigenetic",
        "table": "epigenetic_results",
    },
    "eyes": {
        "path": BASE + "/eyes",
        "payload": {
            "request_id": "shared-eyes",
            "observed_date": "2026-09-24",
            "source": "Optometrist paper",
            "notes": "Authored copy",
            "left": eye(-1.25, -0.5, 90),
            "right": eye(-1, -0.25, 80),
        },
        "manifest": "gideon-wellbeing-eyes",
        "tool": "wellbeing_eye_prescriptions_export",
        "arguments": {},
        "section": "eye_prescriptions",
        "table": "eye_prescription_revisions",
    },
    "lifestyle": {
        "path": BASE + "/lifestyle-profiles",
        "payload": {
            "request_id": "shared-lifestyle",
            "observed_at": "2026-09-25T08:30:00Z",
            "source": "Primary care intake",
            "reported_sex": "male",
            "sex_source": "Patient report",
            "smoking_status": "never",
            "diet_quality": {
                "value": 8,
                "scale": {"minimum": 0, "maximum": 10, "label": "0-10 intake"},
            },
            "stress": {
                "value": 2,
                "scale": {"minimum": 0, "maximum": 10, "label": "0-10 intake"},
            },
            "reported_bmi": 24.2,
            "condition_labels": ["Seasonal allergies"],
            "reported_daily_alcohol": None,
        },
        "manifest": "gideon-lifestyle-profile",
        "tool": "lifestyle_profile_export",
        "arguments": {},
        "section": "lifestyle_profiles",
        "table": "lifestyle_profile_revisions",
    },
    "body": {
        "path": BASE + "/body-composition",
        "payload": {
            "request_id": "shared-body",
            "observed_at": "2026-09-25T08:30:00+02:00",
            "source": "Authored scale transcription",
            "notes": "Morning",
            "values": {
                "muscle_percent": 41.2,
                "fat_percent": 18.4,
                "bone_mass": {"value": 3, "unit": "kg"},
                "temperature": {"value": 37, "unit": "C"},
            },
        },
        "manifest": "gideon-body-composition",
        "tool": "body_composition_read",
        "arguments": {"operation": "export"},
        "section": "body_composition",
        "table": "body_composition_revisions",
    },
}

CORRECTIONS = {
    "epigenetic": {
        "request_id": "shared-epi-correction",
        "revision": 1,
        "biological_age": {"value": 37.9, "unit": "years"},
        "notes": "Corrected transcription",
    },
    "eyes": {
        "request_id": "shared-eyes-correction",
        "revision": 1,
        "observed_date": "2026-09-25",
        "notes": "Cylinder corrected",
        "left": eye(-1.25, -0.75, 95),
        "right": eye(-1, -0.25, 80),
    },
    "lifestyle": {
        "request_id": "shared-lifestyle-correction",
        "revision": 1,
        "smoking_status": "former",
        "reported_bmi": 24.0,
        "reported_daily_alcohol": {"value": 12, "unit": "g_per_day"},
    },
    "body": {
        "request_id": "shared-body-correction",
        "revision": 1,
        "notes": "Corrected scale transcription",
        "values": {
            "muscle_percent": 42,
            "fat_percent": 18,
            "bone_mass": {"value": 3, "unit": "kg"},
            "temperature": {"value": 37, "unit": "C"},
        },
    },
}


@pytest.mark.asyncio
async def test_shared_routes_native_discovery_whole_health_export_and_sqlite_backup(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    app = web.Application()
    register(app, tmp_path)
    registry = ProviderRegistry()
    registry.register_type_handler("tool", ToolTypeHandler())
    created = {}
    async with TestClient(TestServer(app)) as client:
        try:
            for family, contract in FAMILIES.items():
                response = await client.post(contract["path"], json=contract["payload"])
                assert response.status == 201
                created[family] = await response.json()
                assert created[family]["revision"] == 1

            for contract in FAMILIES.values():
                manifest = AppManifest.from_json_file(
                    NATIVE / contract["manifest"] / "app.json"
                )
                registry.register(manifest, enabled=True)
                provider = registry.get(manifest.name).provider_instance
                definitions = {tool.name: tool for tool in await provider.list_tools()}
                assert (
                    contract["tool"] in definitions
                    and definitions[contract["tool"]].requires_approval is False
                )
                result = await provider.invoke(contract["tool"], contract["arguments"])
                assert result.success and len(json.loads(result.output)["history"]) == 1

            exports = ExportStore(tmp_path)
            preview = exports.preview()
            for contract in FAMILIES.values():
                assert preview["counts"][contract["section"]] == 1
            metadata = exports.create({"request_id": "clinical-snapshot"})
            snapshot = json.loads(exports.download(metadata["id"]))
            for family, contract in FAMILIES.items():
                section = snapshot["sections"][contract["section"]]
                assert set(section) == {"history"}
                assert section["history"] == [created[family]]
            encoded = json.dumps(snapshot, sort_keys=True)
            assert "request_id" not in encoded and "_requests" not in encoded

            source = sqlite3.connect(tmp_path / "capabilities/wellbeing.sqlite3")
            backup = sqlite3.connect(tmp_path / "wellbeing-backup.sqlite3")
            try:
                source.backup(backup)
                assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
                for contract in FAMILIES.values():
                    assert backup.execute(
                        f"SELECT count(*) FROM {contract['table']}"
                    ).fetchone() == (1,)
            finally:
                backup.close()
                source.close()
        finally:
            for contract in FAMILIES.values():
                registry.deregister(contract["manifest"])


@pytest.mark.asyncio
async def test_cross_family_corrections_preserve_history_in_whole_health_export_and_home_isolation(
    tmp_path,
):
    owner_home = tmp_path / "owner"
    isolated_home = tmp_path / "isolated"
    owner_app, isolated_app = web.Application(), web.Application()
    register(owner_app, owner_home)
    register(isolated_app, isolated_home)
    originals, corrected = {}, {}
    async with (
        TestClient(TestServer(owner_app)) as owner,
        TestClient(TestServer(isolated_app)) as isolated,
    ):
        for family, contract in FAMILIES.items():
            response = await owner.post(contract["path"], json=contract["payload"])
            assert response.status == 201
            originals[family] = await response.json()
            assert originals[family]["revision"] == 1
            resource = contract["path"] + "/" + originals[family]["id"]
            response = await owner.put(resource, json=CORRECTIONS[family])
            assert response.status == 200
            corrected[family] = await response.json()
            assert corrected[family]["revision"] == 2
            assert corrected[family]["id"] == originals[family]["id"]
            response = await owner.get(resource + "/history")
            assert response.status == 200
            assert await response.json() == {
                "history": [originals[family], corrected[family]]
            }

        response = await isolated.get(BASE + "/exports/preview")
        assert response.status == 200
        empty = await response.json()
        for contract in FAMILIES.values():
            assert empty["counts"][contract["section"]] == 0

        response = await owner.post(
            BASE + "/exports", json={"request_id": "corrected-clinical-snapshot"}
        )
        assert response.status == 200
        metadata = await response.json()
        response = await owner.get(BASE + "/exports/" + metadata["id"] + "/download")
        assert response.status == 200
        snapshot = json.loads(await response.read())
        for family, contract in FAMILIES.items():
            section = snapshot["sections"][contract["section"]]
            assert set(section) == {"history"}
            assert section["history"] == [originals[family], corrected[family]]
            assert section["history"][-1]["revision"] == 2

        with sqlite3.connect(owner_home / "capabilities/wellbeing.sqlite3") as database:
            assert database.execute("SELECT count(*) FROM requests").fetchone()[0] > 0
            assert (
                database.execute(
                    "SELECT count(*) FROM eye_prescription_requests"
                ).fetchone()[0]
                > 0
            )
            assert (
                database.execute(
                    "SELECT count(*) FROM body_composition_requests"
                ).fetchone()[0]
                > 0
            )
        encoded = json.dumps(snapshot, sort_keys=True)
        assert "request_id" not in encoded
        assert "shared-epi-correction" not in encoded
        assert "corrected-clinical-snapshot" not in encoded
