from gideon.integrations.inbox import InboxState, InboxStore
from gideon.integrations.inbox_providers.base import IncomingMessage
from gideon.integrations.inbox_providers.filesystem_source import FilesystemSourceProvider
from gideon.integrations.inbox_service import InboxService


def test_same_provider_id_and_second_remain_distinct_by_channel_and_deduplicate(tmp_path):
    provider = FilesystemSourceProvider()
    store = InboxStore(tmp_path / "inbox.json")
    service = InboxService(
        state=InboxState(tmp_path / "inbox-state.json"), store=store, provider=provider
    )
    messages = [
        IncomingMessage(
            id="same-external-id",
            channel_id=channel,
            channel_name=channel,
            text="A message",
            sender_id="sender",
            timestamp=1700000000.0,
        )
        for channel in ("room-a", "room-b")
    ]

    assert service._ingest(messages) == 2
    assert len(store.items) == 2
    assert len({item.id for item in store.items.values()}) == 2
    assert service._ingest(messages) == 0
