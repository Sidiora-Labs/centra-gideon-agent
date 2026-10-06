"""A denied foreign turn drains the actual native runtime outcome buffer."""

from types import SimpleNamespace

import pytest
from test_background_completion_contract import configured_completion

from gideon.cognition.learning import Cadence, GateDecision, GateReason
from gideon.extensions.providers.provider_bridge import resolve_provider_for_use_case
from gideon.extensions.providers.use_cases import save_active_models
from gideon.interfaces.dashboard.chat_runner import (
    _discard_turn_outcomes,
    _maybe_after_turn_review,
)
from gideon.security.approval_answer import YOU, trigger
from gideon.security.session_credentials import begin_turn, end_turn


@pytest.mark.asyncio
async def test_foreign_outcomes_drained_before_learning_or_next_owner(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    credential = begin_turn(
        "trigger:foreign",
        trigger("foreign"),
        turn_id="foreign-turn",
        memory_mode="persistent",
    )
    runtime = None
    try:
        async with configured_completion(lambda request, n: "unused") as (requests, _):
            save_active_models(
                {"chat": ["ContractSDK:good"], "background": ["ContractSDK:good"]}
            )
            runtime = resolve_provider_for_use_case(
                "chat", session_key="trigger:foreign"
            )
            await runtime.start()
            runtime._record_tool_outcome("foreign_tool", False, "foreign output")
            assert runtime._tool_outcomes == [("foreign_tool", "success")]
            # No context builder is provided: any learning/capture attempt fails the test.
            _maybe_after_turn_review(
                SimpleNamespace(),
                SimpleNamespace(),
                "foreign words",
                "foreign answer",
                1,
                provider=runtime,
                decision=GateDecision(True, True, GateReason.ALLOWED, Cadence.PER_TURN),
            )
            assert runtime.drain_tool_outcomes() == []
            assert requests == []
            end_turn(credential)
            credential = begin_turn(
                "dashboard:next-owner",
                YOU,
                turn_id="next-owner",
                memory_mode="persistent",
            )
            assert runtime.drain_tool_outcomes() == []
            runtime._record_tool_outcome("cancelled_tool", False, "cancelled output")
            _discard_turn_outcomes(runtime)
            assert runtime.drain_tool_outcomes() == []
    finally:
        if runtime is not None:
            await runtime.shutdown()
        end_turn(credential)
