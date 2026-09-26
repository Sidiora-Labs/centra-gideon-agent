"""SlackDeskSettings — the app-owned Slack config (moved out of core AppConfig).

Covers load coercion (hardening lifted from the old core loader) + the one-time
migration of a legacy core config.json "slack" block into the app store.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from slack_desk_runtime.settings import (
    ACTIVATION_MENTION,
    ChannelConfig,
    SlackDeskSettings,
    _validate_tracking_channels,
)


class TestCoercion:
    def test_tracking_channels_coerces_bare_strings_and_drops_junk(self):
        out = _validate_tracking_channels([
            {"channel_id": "C1", "name": "ok"},
            "C2",              # coerced
            "not-a-channel",   # dropped (bad prefix)
            123,               # dropped (not str/dict)
            {"name": "no id"},  # dropped (no channel_id)
        ])
        assert out == [{"channel_id": "C1", "name": "ok"}, {"channel_id": "C2"}]

    def test_load_hardens_fields(self, tmp_path):
        store = {
            "allowed_users": [{"slack_id": "U1"}, {"name": "no id"}, "bad"],
            "open_channels": ["C1", 5],
            "trusted_bot_ids": ["B1", "B2"],
            "allowed_enterprise_ids": ["E1", "X-bad"],
            "reactions": {"done": "white_check_mark", "bad": 5, "off": None},
            "reactions_enabled": False,
            "channels": {"C1": {"activation": "observe"}, "C2": {"activation": "bogus"}},
            "dm_activation": "nonsense",
        }
        with patch("slack_desk_runtime.settings.ProviderSettings.load", return_value=store), \
             patch("slack_desk_runtime.settings.migrate_from_core"):
            s = SlackDeskSettings.load()
        assert s.allowed_users == [{"slack_id": "U1"}]
        assert s.open_channels == ["C1"]
        assert s.trusted_bot_ids == {"B1", "B2"}
        assert s.allowed_enterprise_ids == ["E1"]           # E-prefix only
        assert s.reactions == {"done": "white_check_mark", "off": None}  # bad value dropped
        assert s.reactions_enabled is False
        assert s.channels["C1"].activation == "observe"
        assert s.channels["C2"].activation == ACTIVATION_MENTION  # bad → deny-by-default
        assert s.dm_activation == ACTIVATION_MENTION          # bad → deny-by-default (mention)

    def test_channel_config_dm_default(self, tmp_path):
        s = SlackDeskSettings(dm_activation="review")
        assert s.channel_config("D123").activation == "review"
        assert s.channel_config("C999").activation == ACTIVATION_MENTION

    def test_channel_config_from_dict_bad_activation(self):
        assert ChannelConfig.from_dict({"activation": "xxx"}).activation == ACTIVATION_MENTION


class TestMigrateFromCore:
    @pytest.fixture()
    def marker(self, tmp_path, monkeypatch):
        """Re-point the done-marker at a NON-existent path (the autouse conftest
        fixture pre-creates one to short-circuit migration in unrelated tests)."""
        from slack_desk_runtime import settings as st
        path = tmp_path / ".core_migration_done"
        monkeypatch.setattr(st, "_migration_marker_path", lambda: path)
        return path

    def test_migrates_and_deletes_core_key(self, tmp_path, marker):
        core_path = tmp_path / "config.json"
        core_path.write_text(json.dumps({
            "slack": {
                "allowed_users": [{"slack_id": "U1", "name": "Me"}],
                "tracking_channels": [{"channel_id": "C1"}],
                "command": "pc",
                "observe_max_messages": 200,  # stays in core
            },
            "agent": {"foo": "bar"},
        }))
        saved: dict = {}
        store_state: dict = {}
        def fake_load(_app): return dict(store_state)
        def fake_update(_app, partial): store_state.update(partial); saved.update(partial)
        from slack_desk_runtime import settings as st
        with patch.object(st, "config_path", return_value=core_path), \
             patch.object(st.ProviderSettings, "load", side_effect=fake_load), \
             patch.object(st.ProviderSettings, "update", side_effect=fake_update):
            st.migrate_from_core()
        # behavioral keys moved to the store
        assert saved["allowed_users"] == [{"slack_id": "U1", "name": "Me"}]
        assert saved["command"] == "pc"
        assert "observe_max_messages" not in saved  # observe_* stays in core
        # core slack block: behavioral keys removed, observe_* preserved
        core_after = json.loads(core_path.read_text())
        assert core_after["slack"] == {"observe_max_messages": 200}
        assert core_after["agent"] == {"foo": "bar"}
        # marker set only after the successful core rewrite
        assert marker.is_file()

    def test_noop_when_marker_present(self, tmp_path, marker):
        marker.touch()
        from slack_desk_runtime import settings as st
        with patch.object(st.ProviderSettings, "load") as ld, \
             patch.object(st.ProviderSettings, "update") as upd, \
             patch.object(st, "config_path") as cp:
            st.migrate_from_core()
        ld.assert_not_called()
        upd.assert_not_called()
        cp.assert_not_called()

    def test_marks_done_when_no_slack_desk_block(self, tmp_path, marker):
        core_path = tmp_path / "config.json"
        core_path.write_text(json.dumps({"agent": {"foo": "bar"}}))
        from slack_desk_runtime import settings as st
        with patch.object(st, "config_path", return_value=core_path), \
             patch.object(st.ProviderSettings, "update") as upd:
            st.migrate_from_core()
        upd.assert_not_called()
        assert marker.is_file()  # stop re-reading core each boot

    def test_core_rewrite_failure_is_loud_and_retries(self, tmp_path, marker, caplog):
        """A failed core rewrite must log at ERROR (naming leftover keys), NOT set
        the marker, and leave core intact; the next boot retries and succeeds
        without clobbering keys the store now owns."""
        import logging

        core_path = tmp_path / "config.json"
        core_path.write_text(json.dumps({
            "slack": {"command": "pc", "reactions_enabled": False},
        }))
        store_state: dict = {}
        def fake_load(_app): return dict(store_state)
        def fake_update(_app, partial): store_state.update(partial)
        from slack_desk_runtime import settings as st

        # Boot 1: store write succeeds, core rewrite blows up.
        with patch.object(st, "config_path", return_value=core_path), \
             patch.object(st.ProviderSettings, "load", side_effect=fake_load), \
             patch.object(st.ProviderSettings, "update", side_effect=fake_update), \
             patch.object(st, "atomic_write", side_effect=OSError("disk full")), \
             caplog.at_level(logging.ERROR, logger="slack_desk_runtime.settings"):
            st.migrate_from_core()
        assert not marker.is_file()                      # retry next boot
        assert store_state["command"] == "pc"            # store write landed
        assert json.loads(core_path.read_text())["slack"]["command"] == "pc"  # core untouched
        loud = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert loud and "command" in loud[0].getMessage() and "reactions_enabled" in loud[0].getMessage()

        # User edits the store between boots — the retry must not clobber it.
        store_state["command"] = "edited-by-user"

        # Boot 2: rewrite succeeds; store-present keys win.
        with patch.object(st, "config_path", return_value=core_path), \
             patch.object(st.ProviderSettings, "load", side_effect=fake_load), \
             patch.object(st.ProviderSettings, "update", side_effect=fake_update):
            st.migrate_from_core()
        assert marker.is_file()
        assert store_state["command"] == "edited-by-user"  # not clobbered by stale core copy
        assert "slack" not in json.loads(core_path.read_text())
