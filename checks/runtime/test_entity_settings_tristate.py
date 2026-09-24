"""Real-file coverage of absent, valid and unreadable entity preferences."""

import json
import time

import pytest

from gideon.extensions.providers import entity_routes as er


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setattr("gideon.core.config.loader.config_dir", lambda: tmp_path)
    (tmp_path / "config.json").write_text("{}")
    (tmp_path / "entity_settings").mkdir()
    return tmp_path


def test_absent_and_valid_settings_are_distinct_from_corruption():
    path = er._entity_settings_path("inbox")
    assert er._load_entity_settings("inbox") == {}
    assert er.load_inbox_settings()["auto_cleanup_enabled"] is True
    path.write_text('{"auto_cleanup_enabled": false, "retention_days": 20}')
    assert er._load_entity_settings("inbox") == {
        "auto_cleanup_enabled": False,
        "retention_days": 20,
    }
    assert er.load_inbox_settings()["retention_days"] == 20


@pytest.mark.parametrize("contents", [b"{bad", b"[]", b"null", b"42", b"\xff"])
def test_corruption_disables_cleanup_without_rewriting(contents):
    path = er._entity_settings_path("inbox")
    path.write_bytes(contents)
    assert er._load_entity_settings("inbox") is None
    assert er.load_inbox_settings()["auto_cleanup_enabled"] is False
    assert er.legacy_inbox_alert_fields() == {}
    assert path.read_bytes() == contents


def test_unreadable_directory_is_corrupt_not_absent():
    er._entity_settings_path("inbox").mkdir()
    assert er._load_entity_settings("inbox") is None
    assert er.load_inbox_settings()["auto_cleanup_enabled"] is False


def test_real_inbox_maintenance_preserves_items_until_settings_repaired(home):
    from gideon.integrations.inbox import InboxItem, InboxState, InboxStore
    from gideon.integrations.inbox_service import InboxService

    store = InboxStore(home / "inbox.json")
    item = InboxItem(
        id="old",
        channel="local",
        channel_name="Local",
        thread_ts=None,
        message="Retain during corruption",
        sender_id="owner",
        sender_name="Owner",
        created_at=time.time() - 100 * 86400,
    )
    store.items[item.id] = item
    store.save()
    service = InboxService(state=InboxState(home / "inbox_state.json"), store=store)
    path = er._entity_settings_path("inbox")
    path.write_text("{broken")
    assert service.run_maintenance() == 0
    assert item.id in store.items
    assert path.read_text() == "{broken"
    path.write_text(json.dumps({"auto_cleanup_enabled": True, "retention_days": 90}))
    assert service.run_maintenance() == 1
    assert not store.items


def test_other_callers_apply_explicit_corruption_fallbacks():
    from gideon.assurance.legibility import discover
    from gideon.automation.workflows import pinned
    from gideon.cognition import feedback, onboarding
    from gideon.engine import session_organize
    from gideon.engine.agents import routing
    from gideon.extensions.apps import catalog

    entities = [
        "notifications",
        routing._STORE,
        session_organize._STORE,
        onboarding._ENTITY,
        "feedback",
        discover._ENTITY,
        pinned._ENTITY,
        catalog._APP_UPDATES_ENTITY,
    ]
    for entity in entities:
        er._entity_settings_path(entity).write_text("{broken")
    assert er.load_notifications_settings() == er.NOTIFICATIONS_DEFAULTS
    assert routing._load_store() == {"muted": [], "dismissals": {}}
    assert session_organize._load_store() == {}
    assert onboarding.load_onboarding_state() == onboarding.default_state()
    assert feedback._settings() == {}
    assert discover.load_dismissed() == set()
    assert pinned._load() == []
    assert catalog._load_notified() == {}
    tip = sorted(discover.TIP_IDS)[0]
    assert discover.dismiss(tip) == {tip}
    er._entity_settings_path(discover._ENTITY).write_text("{broken")
    assert discover.clear_dismissed() == set()
