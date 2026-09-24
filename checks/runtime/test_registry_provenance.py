import json

import pytest

from gideon.extensions.apps import catalog


@pytest.mark.parametrize("is_git", [False, True])
def test_registry_provenance_survives_pointer_to_wire(is_git):
    metadata = {
        "maintainer": "Example Maintainers",
        "lastValidated": "2026-09-24",
        "scanVerdict": "warning",
    }
    pointer = catalog._parse_registry(
        json.dumps({"apps": [{"name": "listed-app", **metadata}]})
    )[0]
    entry = catalog._pointer_to_entry(
        "https://example.test/apps.git", pointer, is_git=is_git
    )
    assert entry.to_dict()["registry"] == metadata
    assert entry.sourceKind == ("git" if is_git else "local")


@pytest.mark.parametrize("value", [None, 123, {}, [], ""])
def test_registry_missing_or_malformed_fields_stay_unknown(value):
    pointer = catalog.RegistryPointer.from_dict(
        {
            "name": "listed-app",
            "maintainer": value,
            "lastValidated": value,
            "scanVerdict": value,
        }
    )
    assert pointer is not None
    entry = catalog._pointer_to_entry("/apps", pointer, is_git=False)
    assert entry.registry == {"maintainer": "", "lastValidated": "", "scanVerdict": ""}


def test_nonregistry_catalog_entry_does_not_claim_registry_provenance():
    for source_kind in ("native", "bundled", "local", "git"):
        entry = catalog.CatalogEntry(
            name="direct-app", displayName="Direct app", sourceKind=source_kind
        )
        assert entry.to_dict()["registry"] is None


def test_real_local_registry_catalog_keeps_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "home"))
    source = tmp_path / "registry-source"
    source.mkdir()
    (source / "app-registry.json").write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "name": "provenance-example",
                        "maintainer": "Example Maintainers",
                        "lastValidated": "2026-09-24",
                        "scanVerdict": "clean",
                    }
                ]
            }
        )
    )
    catalog.add_local_source(str(source))
    entries = catalog.available_catalog()["remoteApps"]
    entry = next(item for item in entries if item["name"] == "provenance-example")
    assert entry["registry"] == {
        "maintainer": "Example Maintainers",
        "lastValidated": "2026-09-24",
        "scanVerdict": "clean",
    }
