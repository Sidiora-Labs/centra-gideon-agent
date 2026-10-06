import json

import pytest

from gideon.integrations.inbox import InboxState, InboxStore
from gideon.integrations.inbox_service import InboxService


@pytest.mark.asyncio
async def test_filesystem_source_polls_real_spool_and_deduplicates(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    (tmp_path / "config.json").write_text(
        json.dumps({"inbox": {"enabled": True, "watched_channels": []}}),
        encoding="utf-8",
    )
    spool = tmp_path / "inbox" / "incoming"
    spool.mkdir(parents=True)
    (spool / "batch.json").write_text(
        json.dumps(
            {
                "messages": [
                    {
                        "id": "message-1",
                        "channel_id": "local-room",
                        "channel_name": "Local room",
                        "text": "A real queued message",
                        "sender_id": "sender-1",
                        "sender_name": "Sender",
                        "timestamp": 1700000000.0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    store = InboxStore(tmp_path / "inbox.json")
    service = InboxService(state=InboxState(tmp_path / "inbox-state.json"), store=store)

    await service._poll_once()
    assert len(store.items) == 1
    item = next(iter(store.items.values()))
    assert item.message == "A real queued message"
    assert item.source == "filesystem"
    await service._poll_once()
    assert len(store.items) == 1
