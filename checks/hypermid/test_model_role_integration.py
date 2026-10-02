from __future__ import annotations

import json
import os
import time
from typing import Any

import pytest

from gideon.hypermid.budgets import (
    AdmissionRequest,
    BudgetLedger,
    BudgetLimits,
    OwnerBudgetPolicy,
    ProviderPolicy,
)
from gideon.hypermid.summarizer import model_provider_authority
from gideon.hypermid.usage import UsageAccountingConsumer
from gideon.integrations.llm.credentials import Credential
from gideon.integrations.llm.openai import OpenAIProvider
from gideon.operations.usage_ledger import UsageJournal


pytestmark = pytest.mark.skipif(
    os.environ.get("HYPERMID_REAL_MODEL_TEST") != "1",
    reason="requires an explicitly enabled real model call",
)

_MAX_OUTPUT_TOKENS = 128


@pytest.mark.asyncio
async def test_host_role_uses_real_model_authority_with_bounded_accounting(
    tmp_path, monkeypatch
):
    endpoint = os.environ["GATEWAY_ROUTER"]
    secret = os.environ["GATEWAY_ROUTER_API_KEY"]
    model = os.environ["HYPERMID_TEST_MODEL"]
    session_id = "hypermid-model-role-live"
    monkeypatch.setenv("GIDEON_HOME", str(tmp_path / "gideon-home"))

    policy = OwnerBudgetPolicy(
        owner_id="owner-live",
        project_id="project-live",
        revision=1,
        limits=BudgetLimits(
            max_call_nanodollars=1_000_000_000,
            max_concurrent=1,
            max_hourly_nanodollars=1_000_000_000,
            max_daily_nanodollars=1_000_000_000,
            max_job_nanodollars=1_000_000_000,
        ),
        providers=(ProviderPolicy("centra", model, "gateway"),),
    )
    budget_ledger = BudgetLedger.open(tmp_path / "budgets.sqlite3", (policy,))
    usage_path = tmp_path / "gideon-home" / "usage" / "turns.jsonl"
    accounting = UsageAccountingConsumer(budget_ledger, UsageJournal(usage_path))
    reservation = accounting.reserve(
        AdmissionRequest(
            reservation_id="model-role-live-reservation",
            owner_id="owner-live",
            project_id="project-live",
            job_id="model-role-live-job",
            job_class="host-role",
            provider_id="centra",
            model_id=model,
            region="gateway",
            background=True,
            estimated_input_tokens=64,
            estimated_output_tokens=_MAX_OUTPUT_TOKENS,
            estimated_cost_nanodollars=1_000_000,
            now_ms=int(time.time() * 1000),
        )
    )

    provider = OpenAIProvider(
        model=model,
        credential=Credential(
            name="private-gateway-credential",
            kind="api_key",
            secret=secret,
            source="env",
        ),
        base_url=endpoint,
        max_tokens=_MAX_OUTPUT_TOKENS,
        extra_options={"temperature": 0},
    )
    provider.served_model_ref = f"centra:{model}"
    outbound_requests: list[dict[str, Any]] = []
    original_request = provider._request

    def capture_request(
        messages: list[dict],
        *,
        model: str,
        tools: list[dict] | None = None,
        reasoning_effort: str = "",
    ) -> dict[str, Any]:
        request = original_request(
            messages,
            model=model,
            tools=tools,
            reasoning_effort=reasoning_effort,
        )
        outbound_requests.append(request)
        return request

    monkeypatch.setattr(provider, "_request", capture_request)
    role_frame = {
        "messages": [
            {
                "role": "user",
                "content": "Reply with only the word bounded.",
            }
        ],
        "max_output_tokens": _MAX_OUTPUT_TOKENS,
        "timeout_seconds": 90.0,
        "session_id": session_id,
    }

    try:
        response = await model_provider_authority(provider)(**role_frame)
        cancel_outcome = await provider.cancel(wait_ack_timeout=0.1)
    finally:
        await provider.shutdown()

    reconciliation = accounting.reconcile_summary(
        reservation,
        response.usage,
        ledger_recorded=True,
    )

    assert response.text.strip()
    assert response.usage.provider == "centra"
    assert response.usage.model == model
    assert response.usage.output_tokens is not None
    assert response.usage.output_tokens <= _MAX_OUTPUT_TOKENS
    assert cancel_outcome == "no_turn"

    assert len(outbound_requests) == 1
    outbound = outbound_requests[0]
    assert outbound["max_tokens"] == _MAX_OUTPUT_TOKENS
    assert "tools" not in outbound
    assert not any("approval" in key.lower() for key in outbound)

    assert reconciliation.outcome == "partial"
    assert reconciliation.ledger_recorded is True
    assert reconciliation.actual is not None
    assert reconciliation.actual.output_tokens == response.usage.output_tokens
    assert budget_ledger.reservation(reservation.reservation_id).status == "unknown"

    rows = UsageJournal(usage_path).rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "background"
    assert row["agent"] == "hypermid-summary"
    assert row["session_key"] == session_id
    assert row["provider"] == "centra"
    assert row["model"] == model
    assert row["output_tokens"] == response.usage.output_tokens

    serialized_frames = json.dumps(
        {
            "role_frame": role_frame,
            "outbound_request": outbound,
            "response_usage": response.usage.to_wire(),
            "reconciliation": reconciliation.to_wire(),
        },
        sort_keys=True,
    )
    for forbidden_value in (secret, endpoint, "private-gateway-credential"):
        assert forbidden_value not in serialized_frames
    for forbidden_key in (
        "credential",
        "authorization",
        "api_key",
        "subscription_source",
    ):
        assert forbidden_key not in serialized_frames.lower()
