import pytest

from gideon.hypermid.config import ContextMode
from gideon.hypermid.foundation import Cursor, Digest, Id, Scope
from gideon.hypermid.subagents import (
    ChildUsage,
    GideonContributionAuthorization,
    ModelBudget,
    ParentContributionJournal,
    SubagentContextError,
    SubagentContextRegistry,
    SubagentContribution,
    SubagentSnapshot,
)


def _scope() -> Scope:
    return Scope(Id("owner-1"), Id("project-1"), Id("workspace-1"))


def _budget() -> ModelBudget:
    return ModelBudget(
        context_window_tokens=8_000,
        reserved_output_tokens=1_000,
        max_input_tokens=7_000,
        max_items=100,
        max_images=0,
        baseline_tokens=1_000,
        delta_tokens=500,
        tail_tokens=1_500,
        confidence="measured",
    )


def _snapshot(child: str, item_ids: tuple[str, ...]) -> SubagentSnapshot:
    return SubagentSnapshot.create(
        child_session_id=child,
        parent_session_id="parent-1",
        scope=_scope(),
        spawn_cursor=Cursor(1, 7),
        parent_mode=ContextMode.PRIMARY,
        mode_ceiling=ContextMode.SHADOW,
        provider_profile_digest=Digest.sha256(b"provider-profile"),
        model_budget=_budget(),
        item_ids=item_ids,
    )


def test_child_snapshot_is_immutable_bounded_and_separately_attributed() -> None:
    registry = SubagentContextRegistry()
    snapshot = registry.spawn(_snapshot("child-1", ("item-1", "item-2")))
    assert snapshot.allows_item("item-1") is True
    assert snapshot.allows_item("item-3") is False
    assert snapshot.child_session_id != snapshot.parent_session_id

    with pytest.raises(SubagentContextError) as error:
        registry.spawn(_snapshot("child-1", ("item-3",)))
    assert error.value.code == "CHILD_IDENTITY_CONFLICT"

    registry.record_outcome(
        "child-1",
        _scope(),
        Cursor(1, 3),
        ChildUsage(input_tokens=11, output_tokens=5, cache_read_tokens=2),
    )
    assert registry.usage("child-1", _scope()) == ChildUsage(
        input_tokens=11,
        output_tokens=5,
        cache_read_tokens=2,
    )


def test_contribution_requires_exact_gideon_authorization_and_appends_once() -> None:
    registry = SubagentContextRegistry()
    registry.spawn(_snapshot("child-1", ("item-1",)))
    registry.record_outcome(
        "child-1", _scope(), Cursor(1, 2), ChildUsage(input_tokens=20, output_tokens=8)
    )
    parent = ParentContributionJournal("parent-1", _scope())
    contribution = SubagentContribution.create(
        "child-1", Cursor(1, 2), "bounded child result", "gideon-user-1"
    )
    denied = GideonContributionAuthorization(
        authorization_id=Id("auth-1"),
        authorized_by=Id("another-user"),
        parent_session_id=Id("parent-1"),
        child_session_id=Id("child-1"),
        scope=_scope(),
        expires_at_ms=2_000,
    )
    with pytest.raises(SubagentContextError) as error:
        registry.publish(
            parent_journal=parent,
            parent_item_id="parent-item-1",
            idempotency_key="contribution-1",
            contribution=contribution,
            authorization=denied,
            now_ms=1_000,
        )
    assert error.value.code == "AUTHORIZATION_DENIED"
    assert parent.cursor == Cursor(1, 0)

    allowed = GideonContributionAuthorization(
        authorization_id=Id("auth-2"),
        authorized_by=Id("gideon-user-1"),
        parent_session_id=Id("parent-1"),
        child_session_id=Id("child-1"),
        scope=_scope(),
        expires_at_ms=2_000,
    )
    first = registry.publish(
        parent_journal=parent,
        parent_item_id="parent-item-1",
        idempotency_key="contribution-1",
        contribution=contribution,
        authorization=allowed,
        now_ms=1_000,
    )
    replay = registry.publish(
        parent_journal=parent,
        parent_item_id="parent-item-replayed",
        idempotency_key="contribution-1",
        contribution=contribution,
        authorization=allowed,
        now_ms=1_500,
    )
    assert replay == first
    assert parent.cursor == Cursor(1, 1)
    assert parent.items == (first,)
    assert (
        first.child_snapshot_digest
        == registry.snapshot("child-1", _scope()).source_digest
    )
