"""Private execution restrictions through native runtime and the actual provider wire."""

import pytest
from test_background_completion_contract import configured_completion

from gideon.extensions.providers.provider_bridge import (
    ProviderResolutionError,
    resolve_provider_for_use_case,
)
from gideon.extensions.providers.use_cases import save_active_models
from gideon.integrations.llm_helpers import one_shot_completion
from gideon.security.approval_answer import OWNER, Principal
from gideon.security.execution_lineage import (
    PrivateModelRefused,
    host_runtime_admission,
    requested_model,
)
from gideon.security.session_credentials import (
    begin_child_turn,
    begin_turn,
    current_work,
    end_turn,
)


@pytest.mark.asyncio
async def test_private_actual_native_model_pinned_before_wire(tmp_path, monkeypatch):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.security.session_credentials import bind_execution

    credential = begin_turn(
        "dashboard:private-model",
        Principal(OWNER, "sir"),
        turn_id="actual-turn",
        memory_mode="temporary",
    )
    runtime = None
    try:
        async with configured_completion(
            lambda request, n: "PRIVATE-DONE", chain=("bad", "good")
        ) as (requests, _):
            save_active_models(
                {
                    "chat": ["ContractSDK:good", "ContractSDK:bad"],
                    "background": ["ContractSDK:bad", "ContractSDK:good"],
                }
            )
            with host_runtime_admission(credential):
                runtime = resolve_provider_for_use_case(
                    "chat",
                    session_key=credential.work.session_key,
                    model_override="ContractSDK:good",
                )
                await runtime.start()
            bind_execution(credential, runtime)
            assert current_work().execution_model == "ContractSDK:good"
            assert current_work().allowed_models == ("ContractSDK:good",)
            assert await one_shot_completion("private request") == "PRIVATE-DONE"
            assert [request["model"] for request in requests] == ["good"]
            with pytest.raises(PrivateModelRefused):
                await one_shot_completion("must not leave", model="ContractSDK:bad")
            with pytest.raises(ProviderResolutionError):
                resolve_provider_for_use_case("chat", model_override="ContractSDK:bad")
            assert len(requests) == 1
            from gideon.security.guardrails.failure import FailureMode

            async def announce(_notice):
                return None

            runtime._announce_failover = announce
            assert not await runtime._advance_model_fallback(
                FailureMode.PROVIDER_ERROR, RuntimeError("unsent provider failure")
            )
            child = begin_child_turn(
                "subagent:private-child", credential.work, turn_id="child"
            )
            try:
                assert requested_model() == ("ContractSDK:good", "native")
                assert await one_shot_completion("child request") == "PRIVATE-DONE"
                assert [request["model"] for request in requests] == ["good", "good"]
            finally:
                end_turn(child)
    finally:
        if runtime is not None:
            await runtime.shutdown()
        end_turn(credential)


@pytest.mark.asyncio
async def test_actual_acp_runtime_private_origin_refuses_model_only(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path))
    from gideon.integrations.llm.acp_agent import AcpAgentProvider
    from gideon.security.session_credentials import bind_execution

    credential = begin_turn(
        "dashboard:private-cli",
        Principal(OWNER, "sir"),
        turn_id="cli-turn",
        memory_mode="incognito",
    )
    try:
        # Production ACP adapter identifies the runtime, never its opaque downstream model.
        runtime = AcpAgentProvider(
            command=["/bin/true"],
            runtime_id="cli",
            session_key=credential.work.session_key,
        )
        bind_execution(credential, runtime)
        assert requested_model() == ("", "acp:cli")
        with pytest.raises(PrivateModelRefused):
            await one_shot_completion("must not be sent to another model")
    finally:
        end_turn(credential)
