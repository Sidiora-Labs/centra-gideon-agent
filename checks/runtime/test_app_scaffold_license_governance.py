import argparse
import json

import pytest

from gideon.interfaces.cli.app_new import app_cmd, provider_types, scaffold


@pytest.mark.parametrize("provider_type", provider_types())
def test_generated_license_governance_agrees_across_files(tmp_path, provider_type):
    result = scaffold("sample-app", provider_type, dest=tmp_path, year=2026)
    license_text = (result.path / "LICENSE").read_text()
    manifest = json.loads((result.path / "app.json").read_text())
    readme = (result.path / "README.md").read_text()
    assert "Copyright (c) 2026 <your name>" in license_text
    assert "Copyright (c) 2026 Sample App" not in license_text
    assert license_text.startswith("Apache License")
    assert "Version 2.0, January 2004" in license_text
    assert "http://www.apache.org/licenses/LICENSE-2.0" in license_text
    assert manifest["license"] == "Apache License 2.0"
    assert "Apache License 2.0 — see `LICENSE`." in readme
    assert "MIT —" not in readme


@pytest.mark.parametrize("author", ["", "   ", "Example Author"])
def test_cli_prints_actionable_placeholder_hint_only_when_needed(
    tmp_path, capsys, author
):
    args = argparse.Namespace(
        app_cmd="new",
        name="license-app",
        type="tool",
        dest=str(tmp_path),
        display_name="Display Is Not An Owner",
        description="",
        author=author,
        force=False,
    )
    assert app_cmd(args) == 0
    output = capsys.readouterr().out
    text = (tmp_path / "license-app" / "LICENSE").read_text()
    if author.strip():
        assert "Example Author" in text
        assert "<your name>" not in text
        assert "Replace <your name>" not in output
    else:
        assert "<your name>" in text
        assert "Replace <your name>" in output
        assert str(tmp_path / "license-app" / "LICENSE") in output
        assert "copyright holder’s name" in output
    assert "Display Is Not An Owner" not in text


def test_explicit_author_changes_only_license_attribution(tmp_path):
    unnamed = scaffold("unnamed-app", "tool", dest=tmp_path, year=2026)
    named = scaffold(
        "named-app", "tool", dest=tmp_path, author="Example Author", year=2026
    )
    template = (unnamed.path / "LICENSE").read_text()
    attributed = (named.path / "LICENSE").read_text()
    assert template.replace("<your name>", "Example Author") == attributed
    assert provider_types(), "license coverage must include real provider types"
