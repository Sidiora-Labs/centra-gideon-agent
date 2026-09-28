from __future__ import annotations

from gideon.cognition.onboarding_import.floors import credential_field, strip_secrets


def test_import_and_native_mcp_share_credential_field_classifier():
    assert credential_field("WEATHER_API_TOKEN")
    assert credential_field("client_secret")
    assert not credential_field("REGION")

    clean, skipped = strip_secrets(
        {
            "env": {
                "WEATHER_API_TOKEN": "import-check-value",
                "REGION": "eu",
            },
            "description": "public-service",
        }
    )
    assert clean == {
        "env": {"REGION": "eu"},
        "description": "public-service",
    }
    assert skipped == 1
