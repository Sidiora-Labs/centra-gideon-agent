from __future__ import annotations

import json

from gideon.hypermid.budgets import (
    AdmissionRequest,
    BudgetLedger,
    BudgetLimits,
    OwnerBudgetPolicy,
    ProviderPolicy,
)
from gideon.hypermid.summarizer import SummaryUsage
from gideon.hypermid.usage import UsageAccountingConsumer
from gideon.integrations.llm.events import AgentEvent, EVENT_COMPLETE
from gideon.operations.usage_ledger import UsageJournal


def test_real_budget_reservations_reconcile_into_the_real_usage_journal(tmp_path):
    policy = OwnerBudgetPolicy(
        owner_id="owner-1",
        project_id="project-1",
        revision=1,
        limits=BudgetLimits(
            max_call_nanodollars=1_000_000_000,
            max_concurrent=4,
            max_hourly_nanodollars=4_000_000_000,
            max_daily_nanodollars=8_000_000_000,
            max_job_nanodollars=4_000_000_000,
        ),
        providers=(ProviderPolicy("centra", "reasoner", "local"),),
    )
    budgets = BudgetLedger.open(tmp_path / "budgets.sqlite3", (policy,))
    journal = UsageJournal(tmp_path / "usage" / "turns.jsonl")
    consumer = UsageAccountingConsumer(budgets, journal)

    request = AdmissionRequest(
        reservation_id="reservation-measured",
        owner_id="owner-1",
        project_id="project-1",
        job_id="job-1",
        job_class="summary",
        provider_id="centra",
        model_id="reasoner",
        region="local",
        background=True,
        estimated_input_tokens=120,
        estimated_output_tokens=40,
        estimated_cost_nanodollars=500_000,
        now_ms=1_000_000,
    )
    reservation = consumer.reserve(request)
    terminal = AgentEvent(
        kind=EVENT_COMPLETE,
        input_tokens=100,
        output_tokens=25,
        cost_usd=0.0004,
        duration_ms=80,
        served_model_ref="centra:reasoner",
        tool_meta={
            "usage_reported": True,
            "usage_status": "measured",
            "model_calls": 1,
        },
    )
    measured = consumer.reconcile_terminal_event(
        reservation,
        terminal,
        source="background",
        session_key="session-1",
        agent="hypermid-summary",
        provider="centra",
        model="reasoner",
        provider_request_id="provider-request-1",
    )
    assert measured.outcome == "measured"
    assert measured.actual.cost_nanodollars == 400_000
    assert budgets.reservation("reservation-measured").status == "settled"
    rows = journal.rows()
    assert len(rows) == 1
    assert rows[0]["source"] == "background"
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["output_tokens"] == 25
    assert rows[0]["credential_ref"] is None
    assert rows[0]["subscription_source"] is None

    partial_request = AdmissionRequest(
        reservation_id="reservation-partial",
        owner_id="owner-1",
        project_id="project-1",
        job_id="job-1",
        job_class="summary",
        provider_id="centra",
        model_id="reasoner",
        region="local",
        background=True,
        estimated_input_tokens=80,
        estimated_output_tokens=20,
        estimated_cost_nanodollars=300_000,
        now_ms=1_000_001,
    )
    partial_reservation = consumer.reserve(partial_request)
    partial = consumer.reconcile_summary(
        partial_reservation,
        SummaryUsage("centra", "reasoner", 70, None, 50, "missing"),
        ledger_recorded=True,
    )
    assert partial.outcome == "partial"
    assert budgets.reservation("reservation-partial").status == "unknown"
    assert len(journal.rows()) == 1

    release_request = AdmissionRequest(
        reservation_id="reservation-no-call",
        owner_id="owner-1",
        project_id="project-1",
        job_id="job-1",
        job_class="summary",
        provider_id="centra",
        model_id="reasoner",
        region="local",
        background=True,
        estimated_input_tokens=20,
        estimated_output_tokens=10,
        estimated_cost_nanodollars=100_000,
        now_ms=1_000_002,
    )
    no_call = consumer.no_call(consumer.reserve(release_request))
    assert no_call.outcome == "no_call"
    assert budgets.reservation("reservation-no-call").status == "released"

    serialized = json.dumps(
        [measured.to_wire(), partial.to_wire(), no_call.to_wire()], sort_keys=True
    ).lower()
    for forbidden in ("credential", "authorization", "api_key", "oauth", "secret"):
        assert forbidden not in serialized
