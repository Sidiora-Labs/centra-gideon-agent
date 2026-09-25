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
    return {"sphere": sphere, "sphere_unit": "D", "cylinder": cylinder,
            "cylinder_unit": "D", "axis": axis, "axis_unit": "degrees"}


FAMILIES = {
    "epigenetic": {
        "path": BASE + "/epigenetic",
        "payload": {"request_id": "shared-epi", "source_report_id": "report-2026", "observed_at": "2026-09-20",
                    "source": "Owner uploaded report", "biological_age": {"value": 38.4, "unit": "years"},
                    "chronological_age": {"value": 41, "unit": "years"},
                    "pace_of_aging": {"value": .91, "scale": "years/year"}, "organ_scores": {}, "notes": "Transcribed"},
        "manifest": "gideon-wellbeing-epigenetic", "tool": "wellbeing_epigenetic_export",
        "arguments": {}, "section": "epigenetic", "table": "epigenetic_results",
    },
    "eyes": {
        "path": BASE + "/eyes",
        "payload": {"request_id": "shared-eyes", "observed_date": "2026-09-24", "source": "Optometrist paper",
                    "notes": "Authored copy", "left": eye(-1.25, -.5, 90), "right": eye(-1, -.25, 80)},
        "manifest": "gideon-wellbeing-eyes", "tool": "wellbeing_eye_prescriptions_export",
        "arguments": {}, "section": "eye_prescriptions", "table": "eye_prescription_revisions",
    },
    "lifestyle": {
        "path": BASE + "/lifestyle-profiles",
        "payload": {"request_id": "shared-lifestyle", "observed_at": "2026-09-25T08:30:00Z", "source": "Primary care intake",
                    "reported_sex": "male", "sex_source": "Patient report", "smoking_status": "never",
                    "diet_quality": {"value": 8, "scale": {"minimum": 0, "maximum": 10, "label": "0-10 intake"}},
                    "stress": {"value": 2, "scale": {"minimum": 0, "maximum": 10, "label": "0-10 intake"}},
                    "reported_bmi": 24.2, "condition_labels": ["Seasonal allergies"], "reported_daily_alcohol": None},
        "manifest": "gideon-lifestyle-profile", "tool": "lifestyle_profile_export",
        "arguments": {}, "section": "lifestyle_profiles", "table": "lifestyle_profile_revisions",
    },
    "body": {
        "path": BASE + "/body-composition",
        "payload": {"request_id": "shared-body", "observed_at": "2026-09-25T08:30:00+02:00",
                    "source": "Authored scale transcription", "notes": "Morning",
                    "values": {"muscle_percent": 41.2, "fat_percent": 18.4,
                               "bone_mass": {"value": 3, "unit": "kg"},
                               "temperature": {"value": 37, "unit": "C"}}},
        "manifest": "gideon-body-composition", "tool": "body_composition_read",
        "arguments": {"operation": "export"}, "section": "body_composition", "table": "body_composition_revisions",
    },
}


@pytest.mark.asyncio
async def test_shared_routes_native_discovery_whole_health_export_and_sqlite_backup(tmp_path, monkeypatch):
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
                manifest = AppManifest.from_json_file(NATIVE / contract["manifest"] / "app.json")
                registry.register(manifest, enabled=True)
                provider = registry.get(manifest.name).provider_instance
                definitions = {tool.name: tool for tool in await provider.list_tools()}
                assert contract["tool"] in definitions and definitions[contract["tool"]].requires_approval is False
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
                    assert backup.execute(f"SELECT count(*) FROM {contract['table']}").fetchone() == (1,)
            finally:
                backup.close(); source.close()
        finally:
            for contract in FAMILIES.values():
                registry.deregister(contract["manifest"])
