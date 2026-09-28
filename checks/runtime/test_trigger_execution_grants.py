from __future__ import annotations

import pytest

from gideon.automation.triggers.grants import action_revision, grant, is_granted
from gideon.automation.triggers.models import Trigger
from gideon.automation.triggers.store import TriggerStore
from gideon.security.approval_answer import YOU
from gideon.security.owner_grants import GrantBook


def _trigger(command: str = "printf one") -> Trigger:
    return Trigger(
        id="grant-roundtrip",
        name="Grant roundtrip",
        kind="manual",
        workflow={"inline": {"provider": "shell", "config": {"command": command}}},
        capabilities={"providers": ["shell"]},
    )


def test_action_grant_is_bound_to_exact_action_and_survives_store_reload(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    store = TriggerStore(base_dir=tmp_path / "config")
    trigger = _trigger()

    store.upsert(trigger)
    current = store.get(trigger.id).trigger
    revision = action_revision(current)
    assert revision
    assert not is_granted(current)
    assert grant(current, confirmed_revision=revision, principal=YOU)
    store.upsert(current)

    reloaded = store.get(trigger.id).trigger
    assert is_granted(reloaded)
    assert GrantBook("trigger_actions").holds(trigger.id, revision)

    reloaded.workflow["inline"]["config"]["command"] = "printf two"
    store.upsert(reloaded)
    changed = store.get(trigger.id).trigger
    assert not is_granted(changed)

    changed.workflow["inline"]["config"]["command"] = "printf one"
    store.upsert(changed)
    edited_back = store.get(trigger.id).trigger
    assert not is_granted(edited_back)


def test_row_seal_without_owner_book_does_not_grant(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    trigger = _trigger()
    revision = action_revision(trigger)
    trigger.capabilities["_owner_action_seal"] = {
        "version": 1,
        "provider": "shell",
        "revision": revision,
    }
    assert not is_granted(trigger)


def test_corrupt_grant_book_allows_disable_but_refuses_reenable(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon"))
    store = TriggerStore(base_dir=tmp_path / "config")
    current = _trigger()
    store.upsert(current)
    assert grant(current, confirmed_revision=action_revision(current), principal=YOU)
    store.upsert(current)
    book = GrantBook("trigger_actions")
    book.path.write_text("{", encoding="utf-8")

    current.enabled = False
    current.state = "paused"
    store.upsert(current)
    disabled = store.get(current.id).trigger
    assert disabled.enabled is False
    assert disabled.state == "paused"
    assert "_owner_action_seal" not in disabled.capabilities
    assert "providers" not in disabled.capabilities
    assert book.path.read_text(encoding="utf-8") == "{"

    before = store.path.read_bytes()
    disabled.enabled = True
    with pytest.raises(OSError):
        store.upsert(disabled)
    assert store.path.read_bytes() == before
    assert store.get(current.id).trigger.enabled is False
