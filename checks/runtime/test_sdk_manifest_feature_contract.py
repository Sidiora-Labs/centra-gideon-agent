"""Published SDK schemas drive native manifest validation and compatibility admission."""

from gideon.extensions.apps import manifest as canonical
from gideon.sdk.features import CORE_FEATURE_ADMISSION, CORE_FEATURES, MANIFEST_BOOLEANS
from gideon.sdk.manifest import (
    LAUNCH_INHERITS,
    PROGRAM_YOU_NAME,
    AppManifest,
    Dependencies,
    ExternalPrerequisite,
    ExternalWrite,
    LaunchedProgram,
    SettingCondition,
)


def test_sdk_schema_identity_and_operational_roundtrips():
    for exposed in (
        Dependencies,
        ExternalPrerequisite,
        ExternalWrite,
        LaunchedProgram,
        SettingCondition,
    ):
        assert exposed is getattr(canonical, exposed.__name__)
    prerequisite = ExternalPrerequisite.from_dict(
        {
            "name": "Local program",
            "why": "Process the input",
            "how": "Install the selected program",
        }
    )
    assert prerequisite.to_dict()["how"] == "Install the selected program"
    dependency = Dependencies.from_dict({"commands": ["git"]})
    assert dependency.to_dict()["commands"] == ["git"]
    write = ExternalWrite.from_dict(
        {"path": "~/Notes/Output", "why": "Store exported notes"}
    )
    assert write.validate() == [] and write.to_dict()["path"] == "~/Notes/Output"
    assert ExternalWrite(path="../escape", why="Store output").validate()
    condition = SettingCondition.from_dict({"setting": "inherit", "value": True})
    assert condition.to_dict() == {"setting": "inherit", "value": True}
    launch = LaunchedProgram(
        program=PROGRAM_YOU_NAME,
        why="Run your selected program",
        inherits=list(LAUNCH_INHERITS),
        inheritsWhile=condition,
    )
    assert launch.validate({"inherit"}) == []
    assert LaunchedProgram.from_dict(launch.to_dict()) == launch
    invalid = LaunchedProgram.from_dict(
        dict(launch.to_dict(), inheritsWhile={"setting": "inherit", "value": "false"})
    )
    assert invalid.validate({"inherit"})


def test_sdk_features_control_actual_manifest_admission():
    assert (
        CORE_FEATURE_ADMISSION in CORE_FEATURES and MANIFEST_BOOLEANS in CORE_FEATURES
    )
    manifest = AppManifest.from_dict(
        {
            "name": "sdk-contract",
            "version": "1.0.0",
            "displayName": "SDK Contract",
            "description": "Validate published SDK contracts",
            "author": "Fixture",
            "requiresCoreFeatures": sorted(CORE_FEATURES),
        }
    )
    assert manifest.validate() == []
    assert manifest.core_compatibility().missing == ()
    manifest.requiresCoreFeatures = [
        CORE_FEATURE_ADMISSION,
        MANIFEST_BOOLEANS,
        "unavailable-sdk-contract",
    ]
    refused = manifest.core_compatibility()
    assert refused.missing == ("unavailable-sdk-contract",)
    assert "upgrade Gideon" in refused.reason
