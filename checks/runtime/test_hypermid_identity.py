from __future__ import annotations

import pytest

from gideon.hypermid.foundation import Cursor, Scope
from gideon.hypermid.identity import (
    ContextIdentity,
    IdentityError,
    IdentityKind,
    IdentityRelation,
    IdentityStore,
    RelationKind,
    SourceIdentity,
)


def _digest(character: str) -> str:
    return character * 64


def test_source_identity_replay_is_stable_across_restart_and_provider_changes() -> None:
    scope = Scope(owner_id="owner-1", project_id="project-1", workspace_id="ws-1")
    store = IdentityStore()
    store.bind_session("session-1", scope)
    original = ContextIdentity(
        identity_id="item-1",
        kind=IdentityKind.JOURNAL_ITEM,
        scope=scope,
        session_id="session-1",
        source=SourceIdentity("gideon-event-7", _digest("a")),
    )
    assert store.register_source_item(original) == original

    restored = IdentityStore.from_mapping(store.to_mapping())
    replay_with_new_candidate = ContextIdentity(
        identity_id="item-retry",
        kind=IdentityKind.JOURNAL_ITEM,
        scope=scope,
        session_id="session-1",
        source=SourceIdentity("gideon-event-7", _digest("a")),
    )
    assert (
        restored.register_source_item(replay_with_new_candidate).identity_id == "item-1"
    )
    assert restored.to_mapping() == store.to_mapping()


def test_all_context_objects_use_explicit_stable_ids_and_relations() -> None:
    scope = Scope(owner_id="owner-1", project_id="project-1")
    store = IdentityStore()
    store.bind_session("session-1", scope)
    item = store.register_source_item(
        ContextIdentity(
            identity_id="item-1",
            kind=IdentityKind.JOURNAL_ITEM,
            scope=scope,
            session_id="session-1",
            source=SourceIdentity("source-1", _digest("b")),
        )
    )
    for kind, identity_id in (
        (IdentityKind.TOOL_CALL, "call-1"),
        (IdentityKind.SUMMARY, "summary-1"),
        (IdentityKind.PROJECTION, "projection-1"),
        (IdentityKind.SUBAGENT_SNAPSHOT, "snapshot-1"),
    ):
        identity = ContextIdentity(
            identity_id=identity_id,
            kind=kind,
            scope=scope,
            session_id="session-1",
            relations=(
                IdentityRelation(
                    kind=RelationKind.DERIVED_FROM,
                    item_id=item.identity_id,
                    source_digest=item.source.source_digest,
                ),
            ),
        )
        assert store.register(identity).relations[0].item_id == "item-1"


def test_scope_mismatch_fails_closed_and_workspace_rebind_is_recorded() -> None:
    original_scope = Scope("owner-1", "project-1", "workspace-old")
    next_scope = Scope("owner-1", "project-1", "workspace-new")
    store = IdentityStore()
    store.bind_session("session-1", original_scope)
    store.register_source_item(
        ContextIdentity(
            identity_id="item-1",
            kind=IdentityKind.JOURNAL_ITEM,
            scope=original_scope,
            session_id="session-1",
            source=SourceIdentity("source-1", _digest("c")),
        )
    )

    with pytest.raises(IdentityError, match="already bound") as mismatch:
        store.bind_session("session-1", next_scope)
    assert mismatch.value.code == "SCOPE_MISMATCH"

    record = store.rebind(
        rebind_id="rebind-1",
        session_id="session-1",
        previous_scope=original_scope,
        next_scope=next_scope,
        cursor=Cursor(epoch=3, sequence=8),
        reason="workspace renamed",
    )
    assert record.previous_scope == original_scope
    assert store.identity("item-1", next_scope).scope == next_scope

    replay = store.rebind(
        rebind_id="rebind-1",
        session_id="session-1",
        previous_scope=original_scope,
        next_scope=next_scope,
        cursor=Cursor(epoch=3, sequence=8),
        reason="workspace renamed",
    )
    assert replay == record

    with pytest.raises(IdentityError) as owner_change:
        store.rebind(
            rebind_id="rebind-2",
            session_id="session-1",
            previous_scope=next_scope,
            next_scope=Scope("owner-2", "project-1", "workspace-new"),
            cursor=Cursor(epoch=3, sequence=9),
            reason="owner changed",
        )
    assert owner_change.value.code == "SCOPE_MISMATCH"


def test_source_event_id_cannot_be_replayed_with_changed_digest() -> None:
    scope = Scope("owner-1", "project-1")
    store = IdentityStore()
    store.bind_session("session-1", scope)
    store.register_source_item(
        ContextIdentity(
            identity_id="item-1",
            kind=IdentityKind.JOURNAL_ITEM,
            scope=scope,
            session_id="session-1",
            source=SourceIdentity("source-1", _digest("d")),
        )
    )
    with pytest.raises(IdentityError) as conflict:
        store.register_source_item(
            ContextIdentity(
                identity_id="item-2",
                kind=IdentityKind.JOURNAL_ITEM,
                scope=scope,
                session_id="session-1",
                source=SourceIdentity("source-1", _digest("e")),
            )
        )
    assert conflict.value.code == "SOURCE_IDENTITY_CONFLICT"
