from __future__ import annotations

import json
from pathlib import Path

from gideon.cognition.onboarding_import import ImportCategory, run_import
from gideon.cognition.onboarding_import.sources import claude_code

FIXTURE = Path(__file__).parent / "fixtures" / "agent_tool_homes" / "sampleowner"


def test_claude_fixture_scans_scopes_and_recognized_destinations(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    result = claude_code.scan(FIXTURE / ".claude", isolated_home=FIXTURE)

    assert result.present
    for category in (
        ImportCategory.INSTRUCTIONS,
        ImportCategory.MEMORIES,
        ImportCategory.MCP_SERVERS,
        ImportCategory.SKILLS,
        ImportCategory.AGENTS,
        ImportCategory.PROMPTS,
        ImportCategory.DENIED_COMMANDS,
        ImportCategory.CONVERSATIONS,
    ):
        assert result.counts()[category.value] > 0

    servers = result.by_category(ImportCategory.MCP_SERVERS)
    assert {item.name for item in servers} >= {"context7", "github", "sampleproject-dev-db"}
    assert any(item.origin.startswith("Local scope") for item in servers)
    assert any(item.origin.startswith("Project scope") or "Project" in item.origin for item in servers)
    assert any(item.name == "code-reviewer" for item in result.by_category(ImportCategory.AGENTS))
    assert any(item.name == "handoff" for item in result.by_category(ImportCategory.PROMPTS))
    assert all("/Users/" not in item.key for item in servers)


def test_claude_agent_prompt_and_deny_items_commit_to_native_destinations(tmp_path, monkeypatch):
    home = tmp_path / "gideon-home"
    monkeypatch.setenv("GIDEON_HOME", str(home))
    result = claude_code.scan(FIXTURE / ".claude", isolated_home=FIXTURE)
    selected = [
        item
        for item in result.items
        if item.category in {
            ImportCategory.AGENTS,
            ImportCategory.PROMPTS,
            ImportCategory.DENIED_COMMANDS,
        }
    ]
    report = run_import([result], fingerprints=[item.fingerprint for item in selected])
    assert report.counts()["imported"] == len(selected)
    assert list((home / "prompts").glob("*.yaml"))
    config = json.loads((home / "config.json").read_text(encoding="utf-8"))
    assert config["agents"]
    assert config["security"]["denied_commands"]
