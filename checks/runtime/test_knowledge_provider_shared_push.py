from gideon.cognition.knowledge.store import KnowledgeStore
from gideon.integrations.inbox import InboxStore, emit_shared_knowledge_item
from gideon.integrations.knowledge_providers.base import KnowledgeItem
from gideon.integrations.knowledge_providers.native import create_native_provider
from gideon.integrations.knowledge_providers.registry import (
    configure_shared_item_push,
    register_provider,
    unregister_provider,
)


def test_teammate_shared_item_pushes_through_provider_to_owner_inbox(
    tmp_path, monkeypatch
):
    monkeypatch.setattr("gideon.integrations.inbox.owner_username", lambda: "alice")
    inbox = InboxStore(tmp_path / "inbox.json")
    provider = create_native_provider(KnowledgeStore(str(tmp_path / "knowledge.db")))
    configure_shared_item_push(
        lambda source, item: emit_shared_knowledge_item(None, source, item, store=inbox)
    )
    register_provider(provider)
    try:
        item_id = provider.push_shared_item(
            KnowledgeItem(
                id="team-item-1",
                title="Release finding",
                content="The canary is healthy.",
                metadata={"sharing_policy": "shared", "owner_username": "bob"},
            )
        )
        queued = inbox.pending("alice")
        assert item_id and len(queued) == 1
        assert queued[0].owner == "alice"
        assert queued[0].refs == {
            "knowledge_item": "team-item-1",
            "provider": "native",
            "shared_by": "bob",
            "dedup_key": "shared-knowledge:native:team-item-1",
        }
    finally:
        unregister_provider(provider.name)
        configure_shared_item_push(None)
