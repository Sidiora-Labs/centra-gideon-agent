import asyncio

from gideon.hypermid.hooks import (
    HookDecision,
    HookDispatcher,
    HookEvent,
    HookOutcomeStore,
    HookRegistration,
    SyntheticBlockRef,
)
from gideon.hypermid.models import Cursor, Scope, Trace


def _event(number: int) -> HookEvent:
    return HookEvent(
        event_id=f"event-{number}",
        kind="project",
        scope=Scope("owner-1", "project-1"),
        trace=Trace("trace-1", f"request-{number}"),
        session_id="session-1",
        cursor=Cursor(1, number),
        policy_revision=1,
        phase="pre",
        metadata={"pressure_band": "normal", "provider_token": "secret-value"},
        created_at="2026-10-02T13:00:00Z",
    )


def test_hook_dispatch_is_bounded_redacted_and_reports_retention_gap() -> None:
    async def run() -> None:
        store = HookOutcomeStore(retention=2)
        dispatcher = HookDispatcher(store, failure_policy="deny")

        async def inject(_event: HookEvent) -> HookDecision:
            return HookDecision.inject(
                (SyntheticBlockRef("synthetic-1", "0" * 64, 4),)
            )

        def deny(_event: HookEvent) -> HookDecision:
            return HookDecision.deny("policy_denied")

        dispatcher.register(
            HookRegistration(
                "hook-b", frozenset({"project"}), frozenset({"pre"}), 100, deny
            )
        )
        dispatcher.register(
            HookRegistration(
                "hook-a", frozenset({"project"}), frozenset({"pre"}), 100, inject
            )
        )

        for number in (1, 2):
            event = _event(number)
            assert event.metadata["provider_token"] == "[redacted]"
            result = await dispatcher.dispatch_pre(event, recorded_at_ms=number)
            assert result.allowed is False
            assert result.reason_code == "policy_denied"
            assert result.synthetic_blocks == ()
            assert [record.hook_id for record in result.outcomes] == ["hook-a", "hook-b"]

        replay = await store.read_after(0)
        assert replay.gap is True
        assert replay.next_cursor == 4
        assert len(replay.outcomes) == 2
        assert "secret-value" not in str([record.to_wire() for record in replay.outcomes])

    asyncio.run(run())
