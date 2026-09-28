"""Owner identity lookup keeps each channel scoped before legacy fallback."""

from __future__ import annotations

from gideon.core.config.credentials import owner_id_credential, owner_id_for


def test_channel_owner_ids_are_independent_and_keep_legacy_fallback(monkeypatch):
    for key in (
        "GIDEON_OWNER_ID",
        owner_id_credential("slack"),
        owner_id_credential("telegram"),
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv(owner_id_credential("slack"), "slack-user")
    monkeypatch.setenv(owner_id_credential("telegram"), "telegram-user")
    assert owner_id_for("slack") == "slack-user"
    assert owner_id_for("telegram") == "telegram-user"

    monkeypatch.delenv(owner_id_credential("slack"))
    monkeypatch.setenv("GIDEON_OWNER_ID", "legacy-user")
    assert owner_id_for("slack") == "legacy-user"
    assert owner_id_for("telegram") == "telegram-user"


def test_provider_key_encoding_is_injective_for_case_and_separators():
    keys = {
        owner_id_credential("Slack"),
        owner_id_credential("slack"),
        owner_id_credential("a_b"),
        owner_id_credential("a-b"),
    }
    assert len(keys) == 4
