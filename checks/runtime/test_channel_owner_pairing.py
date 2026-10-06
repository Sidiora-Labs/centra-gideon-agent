"""Owner pairing is hash-only, single-use and persisted outside sender trust."""

from __future__ import annotations

import json

import pytest

from gideon.core.config.credentials import owner_id_for
from gideon.integrations import channel_trust
from gideon.integrations.channel_inbound import admit, reset_admissions
from gideon.integrations.channel_transports import (
    register_transport,
    unregister_transport,
)
from gideon.integrations.channel_transports.base import ChannelMessage


@pytest.fixture
def isolated_owner_pairing(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    monkeypatch.setenv("GIDEON_WORKSPACE", str(tmp_path / "workspace"))
    monkeypatch.setenv("GIDEON_HOSTED", "0")
    monkeypatch.setenv("GIDEON_CREDENTIAL_BACKEND", "dotenv")
    monkeypatch.delenv("GIDEON_OWNER_ID", raising=False)
    return tmp_path


def test_owner_pairing_requires_real_transport_and_consumes_code_once(
    isolated_owner_pairing,
):
    from gideon.integrations.telegram.transport import TelegramTransport

    provider = "telegram"
    sender = "187654321"
    transport = TelegramTransport({})
    register_transport(transport)
    reset_admissions()
    try:
        code = channel_trust.create_owner_pairing_code(provider)
        pairing_file = (
            isolated_owner_pairing / "entity_settings" / "channel_owner_pairing.json"
        )
        record = json.loads(pairing_file.read_text(encoding="utf-8"))[provider]
        assert code not in pairing_file.read_text(encoding="utf-8")
        assert set(record) >= {"code_hash", "epoch", "expires_at", "attempts"}
        assert "secret" not in record

        verdict = admit(
            None,
            provider,
            ChannelMessage(
                channel_id=sender,
                text=code,
                sender=sender,
                message_id="owner-pairing-1",
                metadata={"sender_name": "Owner"},
            ),
            is_dm=True,
        )
        assert not verdict.allowed
        assert verdict.reason == "owner_paired"
        assert verdict.canned_reply
        assert owner_id_for(provider) == sender
        assert not channel_trust.is_allowed_sender(provider, sender)

        status = channel_trust.owner_pairing_status(provider)
        assert status["active"] is False
        assert "code_hash" not in status and "epoch" not in status
        assert "owner_id" not in status

        channel_trust.allow_sender(provider, sender, via="owner")
        assert sender not in {
            row["sender_id"]
            for row in channel_trust.provider_trust(provider)["allowed_senders"]
        }

        replay = admit(
            None,
            provider,
            ChannelMessage(
                channel_id="198765432",
                text=code,
                sender="198765432",
                message_id="owner-pairing-2",
                metadata={"sender_name": "Another sender"},
            ),
            is_dm=True,
        )
        assert replay.reason != "owner_paired"
        assert owner_id_for(provider) == sender
    finally:
        reset_admissions()
        unregister_transport(provider)
