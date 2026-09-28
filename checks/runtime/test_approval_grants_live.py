"""Standing approval decisions use the settings and ceiling in force now."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    from gideon.security.guardrails import ceiling

    home = tmp_path / "gideon-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIDEON_HOME", str(home))
    monkeypatch.delenv(ceiling.CEILING_PATH_ENV, raising=False)
    config_path = home / "config.json"
    config_path.write_text(
        json.dumps({"agent": {"approval_mode": "auto", "approval_timeout_minutes": 5}}),
        encoding="utf-8",
    )
    ceiling_path = home / "governance" / "ceiling.json"
    ceiling_path.parent.mkdir(parents=True, exist_ok=True)

    def write_ceiling(value: str) -> None:
        ceiling_path.write_text(
            json.dumps({"version": 1, "scopes": {"approval": {"value": value}}}),
            encoding="utf-8",
        )

    ceiling.reset_ceiling()
    yield config_path, write_ceiling
    ceiling.reset_ceiling()


def test_grant_and_timeout_settings_are_read_per_decision(isolated_config):
    from gideon.security.approval_grants import (
        approval_mode_now,
        approval_window_secs,
    )

    config_path, _ = isolated_config
    assert approval_mode_now() == "auto"
    assert approval_window_secs() == 300
    config_path.write_text(
        json.dumps({"agent": {"approval_mode": "interactive", "approval_timeout_minutes": 9}}),
        encoding="utf-8",
    )
    assert approval_mode_now() == "interactive"
    assert approval_window_secs() == 540


def test_ceiling_tightening_is_live_and_runtime_widening_is_refused(isolated_config):
    from gideon.security.approval_grants import TRUST, stands

    _, write_ceiling = isolated_config
    write_ceiling("ask")
    assert not stands(TRUST, caller="session-1", audit=False)

    write_ceiling("auto")
    assert not stands(TRUST, caller="session-1", audit=False)

    write_ceiling("auto")
    from gideon.security.guardrails import ceiling

    ceiling.reset_ceiling()
    assert stands(TRUST, caller="session-1", audit=False)
    write_ceiling("ask")
    assert not stands(TRUST, caller="session-1", audit=False)


def test_tool_grant_denies_a_write_even_when_a_standing_grant_exists(isolated_config):
    from gideon.security.approval_grants import TRUST, stands
    from gideon.security.guardrails import ceiling
    from gideon.security.guardrails.policy import TOOL_READ, tool_grant_denial

    _, write_ceiling = isolated_config
    write_ceiling("auto")
    ceiling.reset_ceiling()
    assert stands(TRUST, caller="session-1", audit=False)
    assert tool_grant_denial("delete_file", TOOL_READ)


def test_approval_gates_use_the_live_window_but_keep_explicit_and_unattended_limits(
    isolated_config,
):
    from gideon.automation.workflows.human_input import (
        DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS,
        gate_timeout_secs,
    )

    config_path, _ = isolated_config
    assert gate_timeout_secs({}) == 300
    config_path.write_text(
        json.dumps({"agent": {"approval_timeout_minutes": 9}}),
        encoding="utf-8",
    )
    assert gate_timeout_secs({}) == 540
    assert gate_timeout_secs({"timeout_secs": 7}) == 7
    assert (
        gate_timeout_secs({}, unattended=True)
        == DEFAULT_BACKGROUND_GATE_TIMEOUT_SECS
    )
    assert gate_timeout_secs({"kind": "event"}, mode="blocking") == 1800
