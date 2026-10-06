import pytest

from gideon.extensions.apps.agent_tiers import agent_tier_covers
from gideon.extensions.apps.catalog import CatalogEntry, is_packaged_native
from gideon.extensions.apps.disclosure import changed, describe
from gideon.extensions.apps.manifest import AppManifest, Permissions
from gideon.extensions.apps.permissions import PermissionChecker, agent_tier_shortfall
from gideon.sdk.features import APP_LAUNCH_DISCLOSURE, core_has


def manifest(**overrides):
    return AppManifest.from_dict(
        {
            "name": "contract-app",
            "version": "1.0.0",
            "displayName": "Contract app",
            "description": "Validates app execution contracts",
            **overrides,
        }
    )


@pytest.mark.parametrize("tier", ["text", "read", "tools"])
def test_agent_tier_round_trips_and_checker_names_it(tier):
    app = manifest(
        permissions={"agent": tier, "config": ["voice.auto_speak"], "memory": "shared"}
    )
    assert app.permissions.agent_tier == tier
    assert app.permissions.to_dict()["agent"] == tier
    checker = PermissionChecker("contract-app", app.permissions)
    assert checker.agent_tier() == tier
    assert checker.can_use_agent()
    assert app.permissions.config == ["voice.auto_speak"]
    assert app.permissions.memory == "shared"
    assert not app.validate()


@pytest.mark.parametrize("value", [True, 1, "true", "admin", {}, []])
def test_invalid_agent_permission_never_grants_work(value):
    app = manifest(permissions={"agent": value})
    assert not app.permissions.agent
    assert not PermissionChecker("contract-app", app.permissions).can_use_agent()
    assert any("permissions.agent" in error for error in app.validate())


@pytest.mark.parametrize("value", [False, None, ""])
def test_absent_agent_permission_stays_denied(value):
    assert not manifest(permissions={"agent": value}).permissions.agent


def test_sdk_tier_constructor_rejects_ambiguous_grants():
    with pytest.raises(TypeError):
        Permissions(agent=True)
    with pytest.raises(TypeError):
        Permissions(agent_tier=True)
    with pytest.raises(ValueError):
        Permissions(agent_tier="admin")
    assert agent_tier_covers("tools", "read")
    assert not agent_tier_covers("read", "tools")
    assert not agent_tier_covers("invalid", "text")
    assert agent_tier_shortfall("read", "tools")
    assert not agent_tier_shortfall("tools", "read")


@pytest.mark.parametrize(
    "field", ["storage", "network", "cron", "storageShared", "backgroundTasks"]
)
@pytest.mark.parametrize("value", ["false", "true", 1, [], {}])
def test_manifest_boolean_values_do_not_coerce_into_permissions(field, value):
    app = manifest(permissions={field: value})
    assert not getattr(app.permissions, field)
    assert any(field in error and "true or false" in error for error in app.validate())


def test_nested_and_native_booleans_report_original_field():
    app = manifest(
        native="false",
        crons=[{"name": "daily", "schedule": "0 9 * * *", "silent": "false"}],
        provider={
            "type": "model",
            "implementation": "provider:create",
            "multiInstance": 1,
        },
    )
    assert not app.native
    assert not app.crons[0].silent
    errors = app.validate()
    assert any("native" in error and "true or false" in error for error in errors)
    assert any("silent" in error and "true or false" in error for error in errors)
    assert any(
        "multiInstance" in error and "true or false" in error for error in errors
    )


def test_launch_dependencies_writes_and_condition_reach_catalog_and_review():
    app = manifest(
        dependencies={"npmPackages": ["@scope/adapter"]},
        provider={
            "type": "model",
            "implementation": "provider:create",
            "settingsSchema": {
                "properties": {
                    "folder": {
                        "type": "boolean",
                        "default": False,
                        "x-meta": {"label": "Load folder settings"},
                    }
                }
            },
        },
        launches=[
            {
                "program": "npx",
                "why": "Run the adapter",
                "npmPackage": "@scope/adapter",
                "hosts": ["api.example.com"],
                "inherits": ["folder-settings"],
                "inheritsWhile": {"setting": "folder", "value": True},
            }
        ],
        writes=[{"path": "~/adapter/config.json", "why": "Store adapter settings"}],
    )
    assert not app.validate()
    round_trip = AppManifest.from_dict(app.to_dict())
    disclosure = describe(round_trip)
    assert disclosure["npmPackages"] == ["@scope/adapter"]
    launch = disclosure["launches"][0]
    assert launch["npmPackage"] == "@scope/adapter"
    assert launch["hosts"] == ["api.example.com"]
    assert launch["inheritsWhile"] == {
        "setting": "folder",
        "label": "Load folder settings",
        "value": True,
        "default": False,
    }
    assert disclosure["writes"][0]["path"] == "~/adapter/config.json"
    assert "npx" in disclosure["runsAsYou"]
    assert (
        CatalogEntry(
            name=app.name,
            displayName="Contract",
            launches=disclosure["launches"],
            npmPackages=disclosure["npmPackages"],
            writes=disclosure["writes"],
        ).to_dict()["launches"]
        == disclosure["launches"]
    )
    changed_app = manifest(
        launches=[
            {"program": "git", "why": "Read repository", "hosts": ["git.example.com"]}
        ]
    )
    assert changed(disclosure, describe(changed_app))


@pytest.mark.parametrize(
    "launch",
    [
        {"program": "npx", "why": "Run adapter"},
        {"program": "python", "why": "Run app", "npmPackage": "some-package"},
        {"program": "/bin/bash", "why": "Run app"},
        {
            "program": "git",
            "why": "Read repository",
            "hosts": ["https://git.example.com"],
        },
        {
            "program": "git",
            "why": "Read repository",
            "hosts": ["git.example.com", "git.example.com"],
        },
        {"program": "git", "why": "Read repository", "inherits": ["unknown"]},
    ],
)
def test_launch_review_rejects_incomplete_or_ambiguous_declarations(launch):
    assert manifest(launches=[launch]).validate()


def test_user_selected_program_is_disclosed_without_claiming_a_fixed_name():
    app = manifest(launches=[{"program": "*", "why": "Run your configured tool"}])
    assert not app.validate()
    assert "programs you name" in describe(app)["runsAsYou"]


@pytest.mark.parametrize(
    "packages", [["x;touch bad"], ["package@latest"], ["same", "same"]]
)
def test_npm_dependency_names_cannot_include_options_versions_or_duplicates(packages):
    assert manifest(dependencies={"npmPackages": packages}).validate()


@pytest.mark.parametrize("path", ["/etc/setting", "~/../setting", "../../setting"])
def test_external_writes_cannot_hide_absolute_or_parent_paths(path):
    assert manifest(writes=[{"path": path, "why": "Store settings"}]).validate()


def test_feature_admission_extends_existing_version_floor_without_false_advertising():
    assert core_has(APP_LAUNCH_DISCLOSURE)
    known = manifest(
        requiresCoreFeatures=[APP_LAUNCH_DISCLOSURE], minGideonVersion="1.0.0"
    )
    assert known.core_compatibility("1.1.0").admits
    assert not known.core_compatibility("0.9.0").admits
    missing = manifest(requiresCoreFeatures=["unsupported-app-contract"])
    verdict = missing.core_compatibility("1.1.0")
    assert not verdict.admits
    assert verdict.to_dict()["missing"] == ["unsupported-app-contract"]
    assert "unsupported-app-contract" in verdict.reason
    assert missing.to_dict()["requiresCoreFeatures"] == ["unsupported-app-contract"]
    assert manifest(requiresCoreFeatures=["BadFeature"]).validate()
    assert manifest(requiresCoreFeatures=[APP_LAUNCH_DISCLOSURE] * 2).validate()


def test_native_identity_requires_packaged_manifest():
    assert not is_packaged_native("contract-app")
    assert not is_packaged_native("../contract-app")
    assert not is_packaged_native("contract-app/app.json")
