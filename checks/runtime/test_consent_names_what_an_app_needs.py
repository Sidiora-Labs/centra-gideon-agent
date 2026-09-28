from __future__ import annotations

import json
from pathlib import Path

from gideon.extensions.apps import catalog
from gideon.extensions.apps.disclosure import describe
from gideon.extensions.apps.manifest import AppManifest


def _manifest(**extra):
    return {
        "name": "service-client",
        "version": "1.0.0",
        "displayName": "Service Client",
        "description": "Uses an external image service.",
        "provider": {
            "type": "model",
            "implementation": "provider:create_provider",
            "execution": "sidecar",
        },
        "dependencies": {
            "pythonDependencies": ["core-addon>=1"],
            "sidecarDependencies": ["engine-addon>=2"],
        },
        "requires": [
            {
                "name": "Image service",
                "why": "The app sends requests to this service.",
                "how": "Start the service and add its address in Configure.",
            }
        ],
        **extra,
    }


def test_manifest_disclosure_and_store_card_keep_engine_and_external_needs_separate(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_FIRST_PARTY_APPS_DIR", str(tmp_path / "no-bundled-apps"))
    source = tmp_path / "source"
    app = source / "service-client"
    app.mkdir(parents=True)
    (app / "app.json").write_text(json.dumps(_manifest()), encoding="utf-8")

    manifest = AppManifest.from_json_file(app / "app.json")
    assert manifest.validate() == []
    assert AppManifest.from_dict(manifest.to_dict()).to_dict() == manifest.to_dict()
    disclosure = describe(manifest)
    assert disclosure["sidecarDependencies"] == ["engine-addon>=2"]
    assert disclosure["pythonDependencies"] == [
        {"spec": "core-addon>=1", "coreOwned": False}
    ]
    assert disclosure["requires"][0]["name"] == "Image service"
    assert disclosure["providerExecution"] == "sidecar"

    catalog.add_local_source(str(source))
    [entry] = [item for item in catalog._scan_local_sources() if item.name == "service-client"]
    assert entry.sidecarDependencies == ["engine-addon>=2"]
    assert entry.requires == disclosure["requires"]
    assert entry.providerExecution == "sidecar"


def test_malformed_or_unbounded_engine_and_prerequisite_declarations_are_rejected():
    malformed_requirement = AppManifest.from_dict(
        _manifest(dependencies={"sidecarDependencies": ["--index-url=https://example.invalid"]})
    )
    assert any("valid PEP 508" in error for error in malformed_requirement.validate())

    no_sidecar = _manifest()
    no_sidecar["provider"]["execution"] = "in-process"
    invalid_cross_field = AppManifest.from_dict(no_sidecar)
    assert any("require a provider" in error for error in invalid_cross_field.validate())

    incomplete = AppManifest.from_dict(
        _manifest(requires=[{"name": "Image service", "why": "Remote processing."}])
    )
    assert any("how" in error for error in incomplete.validate())
