import logging

import pytest

from gideon.integrations.acp.client import AcpClient
from gideon.integrations.llm.acp_agent import AcpAgentProvider
from gideon.interfaces.dashboard.chat_runner import _abort_acp_turn


@pytest.mark.asyncio
async def test_abort_uses_the_real_provider_cancellation_contract(caplog):
    provider = AcpAgentProvider(command=["true"])
    with caplog.at_level(logging.WARNING):
        outcome = await _abort_acp_turn(provider, "breaker trip")
    assert outcome == "no_turn"
    assert "provider.cancel is unavailable" not in caplog.text


@pytest.mark.asyncio
async def test_raw_client_is_not_mistaken_for_a_provider_cancel_seam(caplog):
    client = AcpClient(command=["true"])
    with caplog.at_level(logging.WARNING):
        outcome = await _abort_acp_turn(client, "ungated tool call")
    assert outcome is None
    assert (
        "ACP abort after ungated tool call: provider.cancel is unavailable"
        in caplog.text
    )
