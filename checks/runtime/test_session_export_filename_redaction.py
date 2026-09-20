from gideon.interfaces.dashboard import session_export


def test_export_filename_redacts_before_punctuation_sanitizing():
    filename = session_export.export_filename(
        "Deploy AKIAIOSFODNN7EXAMPLE!", "fallback", "md"
    )

    assert filename == "deploy-redacted-credential.md"
    assert "akiaiosfodnn7example" not in filename
