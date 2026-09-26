"""Tests for _get_agent_names() — verifies agent dropdown returns JSON name field, not file stem."""

import json

import pytest

from slack_desk_runtime.events import _get_agent_names


def _write_agent(agents_dir, filename: str, data: dict) -> None:
    (agents_dir / filename).write_text(json.dumps(data))


@pytest.fixture()
def agents_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    d = tmp_path / ".gideon" / "agents"
    d.mkdir(parents=True)
    return d


def test_returns_name_field_not_stem(agents_dir):
    """Dropdown values are the JSON 'name' field, not the file stem."""
    _write_agent(agents_dir, "local-MyPkg-cool-agent.json", {"name": "cool-agent"})
    assert _get_agent_names() == ["cool-agent"]


def test_multiple_agents_sorted(agents_dir):
    _write_agent(agents_dir, "local-B-zebra.json", {"name": "zebra"})
    _write_agent(agents_dir, "local-A-alpha.json", {"name": "alpha"})
    _write_agent(agents_dir, "gideon.json", {"name": "gideon"})
    assert _get_agent_names() == ["alpha", "gideon", "zebra"]


def test_falls_back_to_stem_when_name_missing(agents_dir):
    _write_agent(agents_dir, "legacy-agent.json", {"mcpServers": {}})
    assert _get_agent_names() == ["legacy-agent"]


def test_falls_back_to_stem_on_invalid_json(agents_dir):
    (agents_dir / "broken.json").write_text("{invalid json")
    assert _get_agent_names() == ["broken"]


def test_empty_when_no_agents_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # No .gideon/agents directory exists
    assert _get_agent_names() == []


def test_ignores_non_json_files(agents_dir):
    _write_agent(agents_dir, "real-agent.json", {"name": "real"})
    (agents_dir / "README.md").write_text("not an agent")
    assert _get_agent_names() == ["real"]


def test_falls_back_to_stem_on_non_dict_json(agents_dir):
    """List/scalar JSON must not raise AttributeError.

    ``data.get("name")`` on a non-dict would crash the dropdown; the stem
    is used as the fallback instead.
    """
    (agents_dir / "array-root.json").write_text(json.dumps([1, 2, 3]))
    (agents_dir / "scalar-root.json").write_text(json.dumps("hello"))
    assert _get_agent_names() == ["array-root", "scalar-root"]


def test_falls_back_to_stem_when_name_is_null(agents_dir):
    """``{"name": null}`` must not produce None in the result.

    ``None`` in the names list would crash ``sorted()`` with TypeError, so the
    stem is used instead.
    """
    _write_agent(agents_dir, "null-name.json", {"name": None})
    assert _get_agent_names() == ["null-name"]
