from __future__ import annotations

from pathlib import Path

from gideon.cognition.onboarding_import import ImportCategory, run_import
from gideon.cognition.onboarding_import.sources import codex

FIXTURE = Path(__file__).parent / "fixtures" / "agent_tool_homes" / "sampleowner" / ".codex"


def test_codex_fixture_scans_formats_and_prefers_plain_session_twin(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    result = codex.scan(FIXTURE, environ={}, isolated_home=FIXTURE.parent)

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
    conversations = result.by_category(ImportCategory.CONVERSATIONS)
    assert any(item.key.endswith(".jsonl") for item in conversations)
    assert all(not item.key.endswith(".jsonl.zst") for item in conversations)
    assert any(".jsonl.zst" in path.name for path in codex._session_files(FIXTURE / "sessions"))
    assert any(item.name == "reviewer" for item in result.by_category(ImportCategory.AGENTS))
    assert any(item.name == "triage-issue" for item in result.by_category(ImportCategory.PROMPTS))


def test_codex_compressed_session_import_keeps_call_names_not_tool_outputs(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))
    result = codex.scan(FIXTURE, environ={}, isolated_home=FIXTURE.parent)
    compressed_only = [
        item for item in result.by_category(ImportCategory.CONVERSATIONS)
        if item.path.endswith(".jsonl.zst")
    ]
    assert compressed_only
    item = compressed_only[0]
    conversation, _redactions = codex.read_for_import(item)
    messages = conversation["messages"]
    calls = [row for row in messages if row["role"] == "tool"]
    assert calls
    assert all(row["content"] for row in calls)
    assert all("tool output" not in row["content"].lower() for row in calls)

    report = run_import([result], fingerprints=[item.fingerprint])
    assert report.counts()["imported"] == 1
    assert list((tmp_path / "gideon-home" / "sessions").glob("*.jsonl"))
